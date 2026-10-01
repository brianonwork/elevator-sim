"""The evaluation set: seven buildings and eight presets.

A **preset** is one named traffic pattern (for example "morning rush": everyone rides up
from the lobby) together with how much demand to put on it and which number decides the
winner. **Demand** is stated as a fraction of the fleet's **nominal throughput**, the
arrival rate at which every seat is used exactly once per round trip of the building
(``Building.nominal_rate``); every standard preset runs at 0.75 of it and overload at 1.5,
so a building's arrival rate follows from its configuration alone, with nothing measured. A
**variant** is a run setting, such as stop time, that a preset varies while the building
stays the same. Express service is a scheduler (``elevator_sim.algorithms`` ``express``),
not a variant: one car in four owns every trip inside the lobby plus the top third of the
building, and it runs in every cell like the other six.

Anchor floors scale with building height, so "the hub at floor 30" and "the top band 41-60"
mean the middle and the top third of whatever building they are asked for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..algorithms.policies import top_band
from ..models import BuildingConfig, Request
from .traffic import (
    Arrival,
    Flow,
    Mixture,
    band,
    bursts,
    generate,
    offset,
    point,
    ramp,
    steady,
    uniform,
)


@dataclass(frozen=True)
class Building:
    """Floors, cars and capacity: the three things the take-home makes configurable."""

    name: str
    """Short label used in cache keys and reports, e.g. ``"B1"``."""
    floors: int
    """Number of floors; floors are numbered 1 (the lobby) to ``floors``."""
    cars: int
    """Number of elevator cars in the fleet."""
    capacity: int
    """Passengers one car can hold at once."""
    requests: int
    """Requests generated per run, for presets bounded by request count."""
    seeds: int
    """Independent runs (random seeds) made per preset on this building."""

    @property
    def nominal_rate(self) -> float:
        """Return ``cars * capacity / (2 * floors)``, the fleet's nominal throughput.

        A car takes one tick per floor, so a full sweep up the building and back is
        ``2 * floors`` ticks. If every seat is filled exactly once per such round trip, the
        fleet delivers ``cars * capacity`` riders every ``2 * floors`` ticks. That is the
        throughput of the hardest traffic there is (everyone boards at the lobby and rides
        most of the way up, as in the morning rush); traffic with shorter, overlapping trips
        lets a seat turn over several times per sweep and so can run well above it.

        Returns:
            Riders per tick.
        """
        return self.cars * self.capacity / (2 * self.floors)


BUILDINGS = (
    # Reference building; headline numbers. Every building gets 10 seeds.
    Building("B1", 60, 4, 8, 400, 10),
    # Few cars.
    Building("B2", 60, 2, 8, 200, 10),
    # Many cars.
    Building("B3", 60, 8, 8, 800, 10),
    # Short; expect no change from B1.
    Building("B4", 30, 4, 8, 400, 10),
    # Tall; expect no change from B1.
    Building("B5", 100, 4, 8, 400, 10),
    # Small cars; stranding frequent.
    Building("B6", 60, 4, 4, 400, 10),
    # Big cars; stranding rare.
    Building("B7", 60, 4, 16, 400, 10),
)
BY_NAME = {b.name: b for b in BUILDINGS}
REFERENCE_BUILDING = BUILDINGS[0]
"""The building the headline numbers, the stop-time pair and every figure are read on."""


def hub(floors: int) -> int:
    """Return the interior attractor floor: floor 30 in the 60-floor reference building.

    Args:
        floors: Number of floors in the building.

    Returns:
        The middle floor, 1-based: the lower of the two middle floors for an even floor
        count (floor 30 of 60), the true middle for an odd one (floor 31 of 61).
    """
    # Same as floors // 2 for every even count, which is all of BUILDINGS.
    return (floors + 1) // 2


@dataclass(frozen=True)
class Variant:
    """A run setting a preset varies while the building is held fixed."""

    name: str
    """Label for this setting in results, e.g. ``"plain"`` or ``"stop3"``."""
    stop_time: int = 0
    """Extra ticks a car spends at each stop to let riders on and off."""


PLAIN = Variant("plain")


def steady_arrival(rate: float, span: int) -> Arrival:
    """Build steady arrivals, ignoring the run's length.

    Named apart from ``traffic.steady`` rather than shadowing it, and takes ``span`` it does
    not use so every preset's ``arrival`` has one signature, ramp and bursts included.

    Args:
        rate: Mean requests per tick.
        span: Expected run length in ticks; unused here.

    Returns:
        A Poisson arrival process with mean ``rate`` requests/tick.
    """
    return steady(rate)


STOP1 = Variant("stop1", 1)
"""Stops cost one tick: every cell of the big-bang-1 experiment."""

STOP3 = Variant("stop3", 3)
"""Stops cost three ticks; the setting under which skipping floors can pay."""

EXPERIMENTS = {"big-bang-0": PLAIN.name, "big-bang-1": STOP1.name}
"""The two big-bang experiments, in report order, each mapped to the variant it runs.

Each runs every preset on every building with every scheduler; they differ only in what a stop
costs (0 ticks, then 1). Big-bang-0 is the headline experiment the write-up's counts refer to.
"""


def big_bang_variants(building: Building) -> tuple[Variant, ...]:
    """Return the big-bang variants; the default for presets with no experiment of their own.

    Args:
        building: The building being run; unused, present for a uniform signature.

    Returns:
        ``PLAIN`` (big-bang-0) then ``STOP1`` (big-bang-1).
    """
    return (PLAIN, STOP1)


def big_bang_and_stop_time_pair(building: Building) -> tuple[Variant, ...]:
    """Return the big-bang variants, plus the stop-time pair's 3-tick run on the reference building.

    At stop time 0 skipping floors saves nothing, so the ``express`` scheduler's restriction
    can only cost. The pair (``PLAIN`` against ``STOP3``) exists to show whether express pays
    once stops cost time.

    Args:
        building: The building being run.

    Returns:
        ``PLAIN``, ``STOP1`` and, on the reference building only, ``STOP3``.
    """
    # The 3-tick run is made only on the reference building, to keep the experiments affordable.
    if building.name != REFERENCE_BUILDING.name:
        return (PLAIN, STOP1)
    return (PLAIN, STOP1, STOP3)


def config_for(building: Building, variant: Variant) -> BuildingConfig:
    """Build the engine config for a building and variant.

    Every cell (see ``runner.py``) with this building and variant shares the config; the
    preset, scheduler and seed do not change it.

    Args:
        building: Supplies the floor count, car count and capacity.
        variant: Supplies the stop time.

    Returns:
        A simulator ``BuildingConfig`` ready to run.
    """
    return BuildingConfig(
        num_elevators=building.cars,
        num_floors=building.floors,
        capacity=building.capacity,
        stop_time=variant.stop_time,
    )


# -- flows shared between presets ------------------------------------------------------

def lobby_up(b: Building) -> Flow:
    """Build the "up" flow: trips from the lobby to any floor above it.

    Args:
        b: The building, for its floor count.

    Returns:
        A flow named ``"up"`` from floor 1 to a uniform floor in ``2..floors``.
    """
    return Flow("up", point(1), band(2, b.floors))


def to_lobby(b: Building) -> Flow:
    """Build the "down" flow: trips from any floor above the lobby down to it.

    Args:
        b: The building, for its floor count.

    Returns:
        A flow named ``"down"`` from a uniform floor in ``2..floors`` to floor 1.
    """
    return Flow("down", band(2, b.floors), point(1))


def two_way(b: Building) -> Mixture:
    """Build skewed two-way traffic: a majority riding up, a minority going against it.

    Args:
        b: The building, for its floor count.

    Returns:
        A mixture that is 85% ``"up"`` trips and 15% ``"down"`` trips.
    """
    return ((0.85, lobby_up(b)), (0.15, to_lobby(b)))


@dataclass(frozen=True)
class Preset:
    """One traffic pattern plus how its result is read."""

    number: int
    """Preset number 1-8, as in ``traffic-patterns.md``; also feeds the seed."""
    name: str
    """Short label, e.g. ``"morning-rush"``."""
    mixture: Callable[[Building], Mixture]
    """Builds the weighted flows (where trips go) for a given building."""
    arrival: Callable[[float, int], Arrival]
    """Built from the arrival rate and the run's expected length in ticks."""
    load: float
    """Demand as a fraction of the fleet's nominal throughput (``Building.nominal_rate``).

    Every standard preset uses ``DEMAND`` (0.75) and overload ``OVERLOAD_DEMAND`` (1.5), so
    the arrival rate is ``load * building.nominal_rate`` riders per tick: fixed across
    patterns and derived from the building's configuration alone. See ``rate_for``."""
    horizon: int | None = None
    """Arrivals stop at this tick instead of after a request count; the run then drains until
    everyone is delivered. Only Overload sets it."""
    variants: Callable[[Building], tuple[Variant, ...]] = big_bang_variants
    """Runs to make per building. The first is big-bang-0's, the headline experiment."""
    hypothesis: str = ""
    """One-sentence prediction the preset exists to test, shown in the report."""

    def seed_for(self, building: Building, index: int) -> int:
        """Return a reproducible seed for one run index of this preset on a building.

        The seed ignores the variant, so every variant and scheduler reuses it.

        Every scheduler shares it, which pairs the comparison: the draw cancels out and only
        assignment differs.

        Args:
            building: The building being run; must be one of ``BUILDINGS``.
            index: Which run of this preset on this building, counting from 0.

        Returns:
            ``number * 10_000 + building position * 100 + index``, so no two (preset,
            building, run) triples collide while there are fewer than 100 buildings and 100
            runs per cell.
        """
        return self.number * 10_000 + BUILDINGS.index(building) * 100 + index

    def seeds(self, building: Building) -> list[int]:
        """Return every seed this preset runs on ``building``.

        Args:
            building: The building being run; its ``seeds`` field sets how many.

        Returns:
            One seed per run, from :meth:`seed_for`.
        """
        return [self.seed_for(building, i) for i in range(building.seeds)]

    def span(self, building: Building, rate: float) -> int:
        """Return the expected length of a run in ticks.

        That is the arrival horizon, or how long the request count takes to arrive at ``rate``.
        Arrival shapes are scaled against it.

        Args:
            building: Supplies the request count.
            rate: Arrival rate in requests/tick.

        Returns:
            The span in ticks, at least 1.
        """
        return self.horizon or max(1, round(building.requests / rate))

    def bound(self, building: Building) -> dict[str, int]:
        """Return what ends generation: a tick horizon, or a request count.

        Args:
            building: Supplies the request count when there is no horizon.

        Returns:
            Keyword arguments for ``traffic.generate``: ``{"horizon": ...}`` or
            ``{"count": ...}``.
        """
        return {"horizon": self.horizon} if self.horizon else {"count": building.requests}


DEMAND = 0.75
"""Every standard preset's demand: three quarters of the fleet's nominal throughput, so the
hardest pattern (the morning rush, which runs at about the nominal rate) is stressed but not
past its limit, and every other pattern is proportionally lighter."""
OVERLOAD_DEMAND = 1.5
"""Overload's demand: twice the standard, past what the fleet can clear for lobby traffic."""


def rate_for(preset: Preset, building: Building) -> float:
    """Return the arrival rate this preset runs at on this building, in riders per tick.

    ``preset.load * building.nominal_rate``: a closed-form rule from the building's
    configuration, needing no simulation. On B1 (4 cars, capacity 8, 60 floors) that is
    0.2 riders per tick for every standard preset and 0.4 for overload.

    Args:
        preset: Supplies the demand fraction.
        building: Supplies the nominal throughput.

    Returns:
        The arrival rate in requests/tick.
    """
    return preset.load * building.nominal_rate


# Positional fields in each preset below: number, name, mixture, arrival, load. Every
# preset is judged on the same number, the mean total time (``report.JUDGED_ON``).
PRESETS = (
    Preset(
        1, "baseline",
        lambda b: ((1.0, Flow("interfloor", uniform(), uniform())),),
        steady_arrival, DEMAND,
        hypothesis="All four close; establishes the paired per-seed difference "
                   "others are read against.",
    ),
    # Arrivals ramp up from zero over the first quarter of the expected span
    # (requests / rate), like a real rush; the ramp makes the real run a little longer.
    Preset(
        2, "morning-rush",
        lambda b: ((1.0, lobby_up(b)),),
        lambda rate, span: ramp(rate, round(0.25 * span)), DEMAND,
        hypothesis="round-robin falls behind: it ignores where the returning cars are.",
    ),
    Preset(
        3, "evening-rush",
        lambda b: ((1.0, to_lobby(b)),),
        steady_arrival, DEMAND,
        hypothesis="Capacity bites descending; spreading riders across cars beats piling on.",
    ),
    Preset(
        4, "skewed-two-way", two_way, steady_arrival, DEMAND,
        hypothesis="The fairness knob: eta-cost-fair trades a little mean for a lower p95.",
    ),
    # Four equal flows into and out of the middle floor, from both sides.
    Preset(
        5, "interior-hub",
        lambda b: (
            (0.25, Flow("to-hub-below", band(1, hub(b.floors) - 1), point(hub(b.floors)))),
            (0.25, Flow("to-hub-above", band(hub(b.floors) + 1, b.floors), point(hub(b.floors)))),
            (0.25, Flow("from-hub-down", point(hub(b.floors)), band(1, hub(b.floors) - 1))),
            (0.25, Flow("from-hub-up", point(hub(b.floors)), band(hub(b.floors) + 1, b.floors))),
        ),
        steady_arrival, DEMAND,
        hypothesis="Cars are pulled from both sides; nearest-car over-commits, eta-cost balances.",
    ),
    # Bursts of 20 requests, spaced so the average rate approximately equals ``rate``
    # (the period 20 / rate is rounded to whole ticks).
    Preset(
        6, "lunch-bursts",
        lambda b: ((0.5, lobby_up(b)), (0.5, to_lobby(b))),
        lambda rate, span: bursts(20, max(1, round(20 / rate))), DEMAND,
        hypothesis="Ten lobby riders a burst exceed one car; is max per window flat or climbing?",
    ),
    # Skewed two-way's traffic at twice the standard demand: deliberately more than the
    # fleet can clear, so arrivals stop at tick 1500 rather than after a request count, and
    # the run drains the backlog that built up.
    Preset(
        7, "overload", two_way, steady_arrival, OVERLOAD_DEMAND, horizon=1500,
        hypothesis="Throughput sets how fast the backlog grows and drains; average trip "
                   "time, with the drain included, separates them.",
    ),
    # Short hops of 1-3 floors mixed with lobby-to-top-band trips the express car can serve.
    Preset(
        8, "local-plus-express",
        lambda b: (
            (0.6, Flow("short-hop", uniform(), offset(1, 3))),
            (0.4, Flow("long-haul", point(1), band(*top_band(b.floors)))),
        ),
        steady_arrival, DEMAND, variants=big_bang_and_stop_time_pair,
        hypothesis="The express scheduler lowers mean total time without raising the max, "
                   "and only once stops cost time.",
    ),
)
BY_NUMBER = {p.number: p for p in PRESETS}
BY_PRESET_NAME = {p.name: p for p in PRESETS}


def traffic_for(
    preset: Preset, building: Building, rate: float, seed: int
) -> tuple[list[Request], int]:
    """Build the request file one cell runs on, with the span its arrival shape was built for.

    The single definition of what traffic a (preset, building, rate, seed) means. Every
    scheduler in a cell gets this same file, which is what pairs the comparison, and
    ``elevator-trials gen`` writes exactly what the experiments ran by calling this.

    Args:
        preset: The traffic pattern.
        building: The building to generate for.
        rate: Arrival rate in requests/tick, normally ``rate_for(preset, building)``.
        seed: Random seed; the same seed always yields the same requests.

    Returns:
        ``(requests, span)``: the requests in time order and the expected run length in
        ticks.
    """
    span = preset.span(building, rate)
    requests = generate(
        preset.mixture(building), preset.arrival(rate, span), building.floors, seed,
        **preset.bound(building),
    )
    return requests, span
