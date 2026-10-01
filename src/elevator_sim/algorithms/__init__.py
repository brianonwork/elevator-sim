"""Built-in schedulers.

All seven are destination dispatch on the same direction-committed LOOK controller. They
differ only in how a newly released request is assigned to a car; ``zone-based``
additionally parks idle cars in their home zone, and ``express`` gives its last car, or last
two on a big fleet, every lobby-to-top-band trip and nothing else.

Every built-in gives each new request to one car for good (destination dispatch), and every car then
sweeps up and down, only turning round when nothing is left ahead (LOOK, with the car sticking to
the direction it chose). A car whose oldest waiting rider has waited ``starve_limit`` ticks keeps a
seat free for them, which bounds every rider's wait whichever policy assigned them. A **zone** is
a band of floors a car treats as home, and its **park floor** is where it waits when it has
nothing to do. An **express car** is one the scheduler only ever sends
between the lobby and the top third of the building, and the only kind of car it sends there. See
``controller.py``, ``policies.py`` and ``dispatch.py`` for the details.
"""

from __future__ import annotations

from ..models import BuildingConfig
from ..scheduler import Scheduler, SchedulerFactory
from .dispatch import DestinationDispatch
from .policies import (
    EtaCost,
    Express,
    RoundRobin,
    ZoneBased,
    express_floors,
    nearest_car,
    nearest_car_eta,
    top_band,
    zones,
)

# Scheduler used when the caller does not name one.
DEFAULT_NAME = "eta-cost"


def build_zone_based(config: BuildingConfig) -> Scheduler:
    """Build a zone-based scheduler: a home-zone bias plus parking in the zone centre.

    Park floors are the zone centres, and a zone depends on how many cars share how many
    floors, which is why this factory reads ``config``.

    Args:
        config: The run's configuration; supplies fleet size and floor count.

    Returns:
        A ``DestinationDispatch`` using ``ZoneBased`` assignment and zone-centre parking.
    """
    zone = zones(config.num_elevators, config.num_floors)

    def park(car_id: int) -> int:
        """Return the 1-based floor car ``car_id`` parks on when idle."""
        lo, hi = zone[car_id]
        # Middle of the zone, rounding down for an even-sized zone.
        return (lo + hi) // 2

    return DestinationDispatch(ZoneBased(), park=park)


def express_car_count(num_elevators: int) -> int:
    """Return how many cars the express scheduler reserves: one per four cars, at least one.

    A quarter of the fleet for the trips inside the top third of the building is the
    simplest rule that scales: a two- or four-car bank gives up one car, an eight-car bank
    two. It is a heuristic, not a fit to the data.

    Args:
        num_elevators: Number of cars in the fleet.

    Returns:
        The number of express cars, ``max(1, num_elevators // 4)``.
    """
    return max(1, num_elevators // 4)


def build_express(config: BuildingConfig) -> Scheduler:
    """Build the express scheduler: ``eta-cost`` with the last cars owning the express band.

    The express cars are the ``express_car_count`` highest-numbered cars and their band is
    the lobby plus the top third of the building (``express_floors``), all derived from
    ``config`` so the scheduler needs no settings of its own. A band trip goes to an express
    car and nothing else; every other trip goes to a local car and nothing else. Within each
    side marginal cost picks the car.

    Args:
        config: The run's configuration; supplies fleet size and floor count.

    Returns:
        A ``DestinationDispatch`` using ``Express`` over ``EtaCost(power=1.0)``.

    Raises:
        ValueError: the fleet has one car, which reserving would leave every trip through a
            middle floor with no car to take it; or the building is so short (two floors)
            that the express band is every floor, so no trip is local and the local cars
            would never be used.
    """
    if config.num_elevators < 2:
        raise ValueError(
            f"express needs at least two cars (one express, one local); got "
            f"{config.num_elevators}"
        )
    band = express_floors(config.num_floors)
    if len(band) == config.num_floors:
        raise ValueError(
            f"express needs a floor outside its band for the local cars; with "
            f"{config.num_floors} floors the band is every floor"
        )
    count = express_car_count(config.num_elevators)
    express_cars = frozenset(range(config.num_elevators - count, config.num_elevators))
    policy = Express(EtaCost(power=1.0), express_cars, band)
    return DestinationDispatch(policy)


BUILTINS: dict[str, SchedulerFactory] = {
    # All but zone-based and express ignore the config: the policy is the whole difference
    # between them.
    # power=1.0 greedily picks the car adding the least predicted total journey time;
    # power=2.0 weighs long journeys more ("fair").
    "eta-cost": lambda config: DestinationDispatch(EtaCost(power=1.0)),
    "eta-cost-fair": lambda config: DestinationDispatch(EtaCost(power=2.0)),
    "nearest-car": lambda config: DestinationDispatch(nearest_car),
    "nearest-car-eta": lambda config: DestinationDispatch(nearest_car_eta),
    "round-robin": lambda config: DestinationDispatch(RoundRobin()),
    "zone-based": build_zone_based,
    "express": build_express,
}
"""Scheduler name -> factory that builds a fresh scheduler for a given ``BuildingConfig``."""


def available() -> list[str]:
    """Return the names of the built-in schedulers, sorted.

    Returns:
        The keys of ``BUILTINS`` in alphabetical order.
    """
    return sorted(BUILTINS)


def create(name: str, config: BuildingConfig) -> Scheduler:
    """Build the built-in scheduler registered under ``name`` for ``config``.

    Args:
        name: A key of ``BUILTINS``, e.g. ``"eta-cost"``.
        config: The run's configuration, passed to the factory.

    Returns:
        A new scheduler instance; each call builds a fresh one with its own state.

    Raises:
        KeyError: ``name`` is not a built-in scheduler; the message lists the valid names.
        ValueError: the scheduler cannot run on this fleet (``express`` on one car).
    """
    try:
        factory = BUILTINS[name]
    except KeyError:
        # Re-raise with a helpful message; ``from None`` hides the bare lookup error.
        raise KeyError(f"unknown scheduler {name!r}; available: {available()}") from None
    return factory(config)


__all__ = [
    "BUILTINS", "DEFAULT_NAME", "DestinationDispatch", "EtaCost", "Express", "RoundRobin",
    "ZoneBased", "available", "create", "express_car_count", "express_floors",
    "nearest_car", "nearest_car_eta", "top_band", "zones",
]
