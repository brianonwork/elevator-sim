"""Scheduling interface. Contains no algorithm code.

The engine owns physics and constraints only: releasing requests at their request time,
moving cars one floor per tick, enforcing capacity, holding cars for ``stop_time`` after a
stop, and stamping timestamps. Any car may stop at any floor. Every scheduling decision is
made by a :class:`Scheduler`, which is asked once per tick for a plan covering every car:

* which waiting passengers each car admits at its current floor (any subset, any order),
* which floor each car moves toward.

Nothing else is fixed. Whether a request is bound to one car at release time (destination
dispatch), served by whichever car passes (collective control), reassigned, batched with
other arrivals, or held back for fairness is entirely up to the scheduler. Concrete
implementations are ``BuildingConfig -> Scheduler`` factories; the built-in ones are listed in
:data:`elevator_sim.algorithms.BUILTINS`, which is what ``--scheduler NAME`` selects from.

Jargon: **destination dispatch** means each passenger is assigned to one specific car as
soon as they make their request. **Collective control** is the classic alternative: a car
picks up anyone waiting at a floor it stops at, heading its way.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .models import BuildingConfig, SimulationState


class SchedulingError(Exception):
    """Raised when a scheduler's plan violates an engine constraint."""


class ReplayError(SchedulingError):
    """Raised when a scheduler's own forward replay of a car does not finish.

    A scheduler bug like any other, so it is a ``SchedulingError``: callers that report
    scheduler failures catch it without also catching every unrelated ``RuntimeError``.
    """


@dataclass(frozen=True)
class CarAction:
    """What one car does this tick."""

    target: int | None = None
    """Floor to move one step toward; ``None`` (or the current floor) means stay put.
    Range-checked whether or not the car is held: a held car ignores the move, but an
    out-of-range target is a scheduler bug either way."""
    board: tuple[str, ...] = ()
    """Ids of waiting requests at this car's current floor to admit, in boarding order.
    Every id must be waiting, at this floor, and within capacity."""


@runtime_checkable
class Scheduler(Protocol):
    """Anything with a ``step(state)`` method that returns a plan; the scheduler interface.

    It is a ``Protocol``, so a class does not need to inherit from it: having a matching
    ``step`` method is enough.
    """

    def step(self, state: SimulationState) -> Mapping[int, CarAction]:
        """Return a plan keyed by elevator id.

        Called exactly once per tick, after riders at their destination have alighted and
        before any boarding or movement. Cars absent from the mapping stay put and board
        nobody. ``state`` is an immutable snapshot; the same one is used for every car.

        Args:
            state: The world at the current tick: time, cars and waiting requests.

        Returns:
            One :class:`CarAction` per car the scheduler wants to act, keyed by elevator id.
        """
        ...


SchedulerFactory = Callable[[BuildingConfig], Scheduler]
"""Function that builds a scheduler for a given building; how algorithms are registered."""
