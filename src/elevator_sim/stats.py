"""Passenger summary statistics.

``wait_time = pickup - request``, ``total_time = dropoff - request`` (wait + travel).
Min, max and mean are what the take-home asks for. The rest describe the shape of the
spread: the median and quartiles (see :class:`Distribution`), the standard deviation, and
p95, added because max rests on a single rider and the trial protocol reads the tail
against it. :class:`FleetSummary` covers system behaviour rather than passenger time: how
the work split across cars, how much each car moved, and how long the queue grew.

"p95" is the 95th percentile: the time that 95% of passengers beat or matched. Unlike the
max, one unlucky rider cannot move it much -- once there are at least 20 passengers. Below
that, nearest-rank p95 is the max.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from statistics import mean, pstdev

from .models import Passenger


def percentile(values: Sequence[int], q: float) -> int:
    """Return the nearest-rank percentile, e.g. ``q=95`` for p95.

    Nearest-rank means the answer is always one of the input values (no interpolation):
    the smallest value with at least ``q`` percent of the data at or below it.

    Args:
        values: The data, in any order.
        q: Percentile to take, from 0 to 100.

    Returns:
        The chosen value from ``values``.

    Raises:
        ValueError: ``values`` is empty.
    """
    if not values:
        raise ValueError("percentile needs a non-empty sequence")
    ordered = sorted(values)
    # Rank is 1-based and at least 1 (so q=0 gives the minimum); subtract 1 to index.
    # Fraction keeps q * n / 100 exact: in floats 0.07 * 100 is 7.000000000000001, which
    # would round the rank up. str(q) turns 95.0 into the exact decimal 95.
    rank = math.ceil(Fraction(str(q)) * len(ordered) / 100)
    return ordered[max(1, rank) - 1]


@dataclass(frozen=True)
class Distribution:
    """How one time (wait, travel or total) is spread over the delivered passengers, in ticks.

    The five-number summary (min, q1, median, q3, max) plus p95, mean and standard deviation.
    Every percentile is nearest-rank, so each is a time some passenger actually had.
    """

    min: int
    """Shortest time."""
    q1: int
    """First quartile: a quarter of passengers had this time or less."""
    median: int
    """The middle passenger's time; half had this or less. Unlike the mean, a few very long
    times cannot pull it up, so a mean well above the median signals a long tail."""
    q3: int
    """Third quartile: three quarters of passengers had this time or less."""
    p95: int
    """95th percentile: the time 95% of passengers beat or matched."""
    max: int
    """Longest time."""
    mean: float
    """Average time."""
    sd: float
    """Population standard deviation: the typical distance of a time from the mean. The
    passengers of a run are the whole group being described, not a sample of it."""

    @property
    def iqr(self) -> int:
        """Interquartile range, ``q3 - q1``: the spread of the middle half of passengers.

        Waits cannot go below 0 but can run long, so this reads more honestly than ``sd``,
        which treats both sides of the mean alike.
        """
        return self.q3 - self.q1


def distribution(values: Sequence[int]) -> Distribution:
    """Describe a list of times.

    Args:
        values: Times in ticks, in any order.

    Returns:
        Their five-number summary, p95, mean and population standard deviation.

    Raises:
        ValueError: ``values`` is empty.
    """
    if not values:
        raise ValueError("distribution needs a non-empty sequence")
    return Distribution(
        min=min(values), q1=percentile(values, 25), median=percentile(values, 50),
        q3=percentile(values, 75), p95=percentile(values, 95), max=max(values),
        mean=mean(values), sd=pstdev(values),
    )


@dataclass(frozen=True)
class Summary:
    """Wait, travel and total-time statistics for one run's delivered passengers."""

    count: int
    """Passengers delivered."""
    unserved: int
    """Passengers still waiting or riding when the run ended. Non-zero only under a horizon."""
    wait: Distribution
    """Wait: request to pickup."""
    travel: Distribution
    """Ride: pickup to drop-off."""
    total: Distribution
    """Total time: request to drop-off (wait plus ride)."""


def summarize(passengers: Iterable[Passenger]) -> Summary:
    """Summarise completed passengers, counting the rest.

    Only passengers who reached their destination feed the statistics; anyone still
    waiting or riding (a run cut short) is only counted in ``unserved``.

    Args:
        passengers: Every passenger from a run, finished or not.

    Returns:
        The statistics for the finished passengers.

    Raises:
        ValueError: no passenger completed their trip.
    """
    waits: list[int] = []
    travels: list[int] = []
    totals: list[int] = []
    unserved = 0
    for p in passengers:
        if p.is_done:
            # A finished passenger always has every timestamp, so these are never None.
            assert (
                p.wait_time is not None and p.travel_time is not None
                and p.total_time is not None
            )
            waits.append(p.wait_time)
            travels.append(p.travel_time)
            totals.append(p.total_time)
        else:
            unserved += 1
    if not waits:
        raise ValueError("no completed passengers to summarise")
    return Summary(
        count=len(waits), unserved=unserved,
        wait=distribution(waits), travel=distribution(travels), total=distribution(totals),
    )


_COLUMNS = ("min", "q1", "median", "q3", "p95", "max", "mean", "sd")
"""Column order of the table ``format_summary`` prints."""


def format_summary(s: Summary) -> str:
    """Return a short human-readable report of a summary for the terminal.

    Args:
        s: The statistics to show.

    Returns:
        A few lines of text (no trailing newline): a count, then one table row per time.
    """
    lines = [f"Passengers served: {s.count}"]
    # Only mention unfinished passengers when there are some.
    if s.unserved:
        lines.append(f"Still outstanding: {s.unserved}")
    # Whole ticks for the order statistics, two decimals for mean and sd; 12 characters of
    # row label so the columns line up under the header.
    lines.append(" " * 12 + "".join(f"{c:>8}" for c in _COLUMNS))
    for label, d in (("Wait time", s.wait), ("Travel time", s.travel), ("Total time", s.total)):
        cells = [d.min, d.q1, d.median, d.q3, d.p95, d.max]
        lines.append(
            f"{label:<12}" + "".join(f"{v:>8}" for v in cells)
            + f"{d.mean:>8.2f}{d.sd:>8.2f}"
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class FleetSummary:
    """How the fleet behaved over a run, as opposed to how long passengers took."""

    riders: tuple[int, ...]
    """Passengers each car picked up, indexed by elevator id. A lopsided split shows one car
    doing most of the work (the express car, a zone car at the lobby)."""
    moving: tuple[int, ...]
    """Ticks each car spent moving, indexed by elevator id; the rest it stood still, to
    board or alight, while held, or idle."""
    ticks: int
    """Ticks in which a car could have moved: every logged tick but the last, whose move
    nothing records. The denominator for ``moving``."""
    peak_waiting: int
    """Most passengers released but not yet aboard at any one tick. A peak close to the
    number of passengers means the fleet fell behind and never caught up."""
    peak_waiting_time: int
    """First tick at which ``peak_waiting`` was reached."""


def fleet_summary(
    passengers: Iterable[Passenger], positions_log: Sequence[tuple[int, tuple[int, ...]]]
) -> FleetSummary:
    """Summarise car workload and queue length from a run's logs.

    Args:
        passengers: Every passenger from a run, finished or not.
        positions_log: ``(time, floors)`` per tick, floors ordered by elevator id, starting
            at time 0 (a :class:`~elevator_sim.simulation.SimulationResult`'s log).

    Returns:
        Riders and moving ticks per car, and the peak of the waiting pool.

    Raises:
        ValueError: ``positions_log`` is empty.
    """
    if not positions_log:
        raise ValueError("fleet_summary needs a non-empty positions log")
    cars = len(positions_log[0][1])
    riders = [0] * cars
    # The waiting pool grows by one at each request time and shrinks by one at each pickup.
    changes: dict[int, int] = {}
    for p in passengers:
        if p.elevator_id is not None:
            riders[p.elevator_id] += 1
        changes[p.request.time] = changes.get(p.request.time, 0) + 1
        if p.pickup_time is not None:
            changes[p.pickup_time] = changes.get(p.pickup_time, 0) - 1
    # A car moved during tick t - 1 if its floor at t differs from its floor at t - 1.
    moving = [0] * cars
    for (_, before), (_, after) in zip(positions_log, positions_log[1:], strict=False):
        for i in range(cars):
            if before[i] != after[i]:
                moving[i] += 1
    # Walk the changes in time order; a passenger released and picked up on the same tick
    # never counts as waiting.
    peak, peak_time, pool = 0, 0, 0
    for t in sorted(changes):
        pool += changes[t]
        if pool > peak:
            peak, peak_time = pool, t
    return FleetSummary(
        riders=tuple(riders), moving=tuple(moving), ticks=len(positions_log) - 1,
        peak_waiting=peak, peak_waiting_time=peak_time,
    )


def format_fleet(f: FleetSummary) -> str:
    """Return a short human-readable report of fleet behaviour for the terminal.

    Args:
        f: The fleet summary to show.

    Returns:
        A few lines of text (no trailing newline): one row per car, then the peak queue.
    """
    lines = [f"{'Car':<12}{'riders':>8}{'moving':>8}"]
    for i, (count, moved) in enumerate(zip(f.riders, f.moving, strict=True)):
        # Share of the run's ticks the car spent travelling; a one-tick run has none.
        share = moved / f.ticks if f.ticks else 0.0
        lines.append(f"{f'elevator_{i}':<12}{count:>8}{share:>8.0%}")
    lines.append(f"Peak waiting: {f.peak_waiting} passenger(s) at tick {f.peak_waiting_time}")
    return "\n".join(lines)
