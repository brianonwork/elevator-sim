"""Traffic generation: flows, arrival processes, and the CSV the CLI reads.

A pattern is a **flow** (where trips go) paired with an **arrival process** (when they
appear), per ``docs/write-up/4-trial-design.md``. A **mixture** is several flows with
weights, e.g. 85% lobby-to-upstairs and 15% upstairs-to-lobby. **Poisson arrivals** means
each tick gets a random whole number of new requests whose long-run average is the chosen
rate, the standard model for independent people turning up at random. Everything here is
deterministic in its seed, so a trial can be re-run exactly and every scheduler can be
paired on identical requests.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from random import Random

from ..csv_io import REQUEST_COLUMNS
from ..models import Request

Dist = Callable[[Random, int, "int | None"], int]
"""``(rng, num_floors, source) -> floor``. ``source`` is ``None`` when sampling a source."""

RESAMPLE_LIMIT = 50
"""Tries to draw a destination away from the source before giving up."""


# -- flow ------------------------------------------------------------------------------

def point(floor: int) -> Dist:
    """Build a floor distribution that is always one floor: a lobby, a hub, a convoy endpoint.

    Args:
        floor: The 1-based floor every draw returns.

    Returns:
        A distribution that ignores its inputs and returns ``floor``.
    """
    return lambda rng, floors, source: floor


def band(lo: int, hi: int) -> Dist:
    """Build a distribution uniform over the contiguous range ``lo..hi``.

    Args:
        lo: Lowest floor, 1-based, inclusive.
        hi: Highest floor, 1-based, inclusive.

    Returns:
        A distribution drawing each floor in the range with equal chance.
    """
    return lambda rng, floors, source: rng.randint(lo, hi)


def uniform() -> Dist:
    """Build a distribution uniform over every floor in the building.

    Returns:
        A distribution drawing each floor ``1..num_floors`` with equal chance.
    """
    return lambda rng, floors, source: rng.randint(1, floors)


def offset(lo: int = 1, hi: int = 3) -> Dist:
    """Build a distribution: source plus ``lo..hi`` floors in a random direction, clamped.

    A destination shape only: it has nothing to say about where a trip starts.

    Args:
        lo: Fewest floors to move away from the source.
        hi: Most floors to move away from the source.

    Returns:
        A destination distribution; clamping to floors ``1..num_floors`` can make a draw
        land on the source, which ``Flow.sample`` then redraws.
    """

    def sample(rng: Random, floors: int, source: int | None) -> int:
        """Draw a floor near ``source``, clamped to the building."""
        assert source is not None, "offset() is a destination distribution"
        return min(floors, max(1, source + rng.randint(lo, hi) * rng.choice((1, -1))))

    return sample


@dataclass(frozen=True)
class Flow:
    """One kind of trip. ``name`` labels it in presets and error messages."""

    name: str
    """Short label, e.g. ``"up"``; not written to the requests."""
    source: Dist
    """Where trips start."""
    dest: Dist
    """Where trips end, possibly depending on the source."""

    def sample(self, rng: Random, floors: int) -> tuple[int, int]:
        """Draw one ``(source, dest)`` pair, redrawing a destination that lands on the source.

        Args:
            rng: The seeded random source.
            floors: Number of floors in the building.

        Returns:
            ``(source, dest)``, 1-based floors that always differ.

        Raises:
            ValueError: No destination away from the source in ``RESAMPLE_LIMIT`` tries,
                e.g. a flow whose source and destination are the same single floor.
        """
        source = self.source(rng, floors, None)
        for _ in range(RESAMPLE_LIMIT):
            dest = self.dest(rng, floors, source)
            if dest != source:
                return source, dest
        raise ValueError(f"flow {self.name!r}: no destination away from floor {source}")


Mixture = Sequence[tuple[float, Flow]]
"""Weighted flows. Each request picks a flow by weight, then samples from it."""


def pick(rng: Random, mixture: Mixture) -> Flow:
    """Choose one flow by weight. Weights need not sum to one.

    Args:
        rng: The seeded random source.
        mixture: ``(weight, flow)`` pairs.

    Returns:
        The chosen flow.

    Raises:
        ValueError: the weights sum to zero or less, so no flow can be chosen.
    """
    total = sum(weight for weight, _ in mixture)
    if total <= 0:
        raise ValueError(f"mixture has no positive weight (total {total})")
    # Draw a point on [0, total weight) and walk the flows until it falls inside one.
    draw = rng.random() * total
    for weight, flow in mixture:
        draw -= weight
        if draw < 0:
            return flow
    # Only reached through floating-point rounding at the very top of the range.
    return mixture[-1][1]


# -- arrivals --------------------------------------------------------------------------

Arrival = Callable[[Random, int], int]
"""``(rng, tick) -> how many requests arrive on this tick``."""


def poisson(rng: Random, mean: float) -> int:
    """Draw a Poisson-distributed count by Knuth's method.

    Means here are per-tick arrival rates, at most about 3, so the loop is short.

    Args:
        rng: The seeded random source.
        mean: Expected count; zero or less always gives 0.

    Returns:
        A non-negative count.
    """
    if mean <= 0:
        return 0
    # Multiply uniform draws until the product drops below e^-mean; the number of draws
    # before that happens is Poisson-distributed.
    limit, count, product = math.exp(-mean), 0, 1.0
    while True:
        product *= rng.random()
        if product <= limit:
            return count
        count += 1


def steady(rate: float) -> Arrival:
    """Build Poisson arrivals with mean ``rate``, so two requests can share a tick as in the spec.

    Args:
        rate: Mean requests per tick.

    Returns:
        An arrival process with the same mean on every tick.
    """
    return lambda rng, tick: poisson(rng, rate)


def bursts(size: int, period: int) -> Arrival:
    """Build arrivals of ``size`` requests on the same tick, every ``period`` ticks.

    Args:
        size: Requests per burst.
        period: Ticks between bursts; the first burst is at tick 0.

    Returns:
        A deterministic arrival process (it never draws from ``rng``).
    """
    return lambda rng, tick: size if tick % period == 0 else 0


def ramp(rate: float, climb: int) -> Arrival:
    """Build Poisson arrivals whose mean climbs from zero to ``rate`` over ``climb`` ticks.

    After the climb the mean holds at ``rate``. The climb is counted in ticks rather than
    in requests: a ramp that started from a share of the requests already emitted could
    never leave zero.

    Args:
        rate: Mean requests per tick once the climb is over.
        climb: Ticks the rise takes; 0 behaves like 1.

    Returns:
        An arrival process whose mean grows linearly with the tick, then stays at ``rate``.
    """
    return lambda rng, tick: poisson(rng, rate * min(1.0, tick / max(1, climb)))


# -- generation ------------------------------------------------------------------------

def generate(
    mixture: Mixture,
    arrival: Arrival,
    floors: int,
    seed: int,
    *,
    count: int | None = None,
    horizon: int | None = None,
    max_ticks: int = 200_000,
) -> list[Request]:
    """Generate requests, bounded by ``count`` or ``horizon``.

    Count-bounded is the default protocol; horizon-bounded is what Overload needs, whose
    demand outruns the fleet: arrivals stop at the horizon, and the run then drains.

    Args:
        mixture: Weighted flows each request is drawn from.
        arrival: How many requests appear on each tick.
        floors: Number of floors in the building.
        seed: Random seed; the same seed gives the same requests.
        count: Stop after this many requests; ``None`` when bounded by ``horizon``.
        horizon: Stop at this tick; ``None`` when bounded by ``count``.
        max_ticks: Safety cap on ticks, so an arrival process that emits too little cannot
            loop forever.

    Returns:
        Requests in time order with ids ``p00000``, ``p00001``, ...

    Raises:
        ValueError: Both or neither of ``count`` and ``horizon`` were given; the bound was
            still not met after ticks ``0 .. max_ticks - 1`` (so ``horizon`` may be at most
            ``max_ticks``); a mixture has no positive weight (from ``pick``); or a flow could
            not draw a destination away from its source (from ``Flow.sample``).
    """
    if (count is None) == (horizon is None):
        raise ValueError("pass exactly one of count= or horizon=")
    # One RNG drives every draw in a fixed order, which is what makes the output a pure
    # function of the seed.
    rng = Random(seed)
    requests: list[Request] = []
    for tick in range(max_ticks):
        # Check both bounds at the top of each tick.
        if count is not None and len(requests) >= count:
            return requests
        if horizon is not None and tick >= horizon:
            return requests
        for _ in range(arrival(rng, tick)):
            # A burst can overshoot the request count; stop mid-tick at exactly ``count``.
            if count is not None and len(requests) >= count:
                break
            flow = pick(rng, mixture)
            source, dest = flow.sample(rng, floors)
            request_id = f"p{len(requests):05d}"
            requests.append(Request(tick, request_id, source, dest))
    # The loop checks bounds at the top of each tick, so one met during the last tick
    # is only seen here.
    if count is not None and len(requests) >= count:
        return requests
    if horizon is not None and max_ticks >= horizon:
        return requests
    wanted = f"{count} requests" if count is not None else f"tick {horizon}"
    raise ValueError(
        f"only {len(requests)} request(s) within {max_ticks} ticks, short of {wanted}; "
        f"is the rate zero?"
    )


# -- csv -------------------------------------------------------------------------------

def write_requests(path: str | Path, requests: Iterable[Request]) -> None:
    """Write requests in the CLI's own ``time,id,source,dest`` format.

    Args:
        path: CSV file to create or overwrite.
        requests: The requests to write, in the order given.
    """
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(REQUEST_COLUMNS)
        for r in requests:
            writer.writerow([r.time, r.id, r.source, r.dest])
