"""Core domain types.

Plain data containers with derived properties only. All behaviour (movement, boarding,
scheduling) lives in the simulation engine and scheduler modules so that these types stay
neutral toward any particular scheduling algorithm.

Two kinds of types live here:

* engine-internal, mutable state (:class:`Passenger`, :class:`Elevator`);
* frozen views handed to schedulers (:class:`Rider`, :class:`ElevatorView`,
  :class:`SimulationState`). A scheduler can never reach the engine's live objects.

Conventions used throughout: time is measured in whole **ticks** (one tick is the time a car
takes to move one floor), and floors are **1-based** (the ground floor is 1). Elevator ids
are 0-based indexes into the fleet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Direction(IntEnum):
    """A direction of travel: a request's, or a scheduler's committed heading.

    The engine itself tracks no direction for a car; direction logic belongs to the scheduler.
    """

    UP = 1
    DOWN = -1
    IDLE = 0

    @classmethod
    def toward(cls, origin: int, dest: int) -> Direction:
        """Return which way something on ``origin`` must travel to reach ``dest``.

        ``IDLE`` when they are the same floor, so callers that have already excluded
        that case get a two-valued answer and the rest get a meaningful one.

        Args:
            origin: Floor the traveller starts on (1-based).
            dest: Floor the traveller wants to reach (1-based).

        Returns:
            ``UP`` if ``dest`` is higher, ``DOWN`` if lower, ``IDLE`` if they are equal.
        """
        if dest > origin:
            return cls.UP
        return cls.DOWN if dest < origin else cls.IDLE


@dataclass(frozen=True)
class Request:
    """A passenger's travel request, submitted at ``time``."""

    time: int
    """Tick at which the passenger presses the call button (>= 0)."""
    id: str
    """Passenger id; must be unique within a run (checked by ``validate_requests``)."""
    source: int
    """Floor the passenger boards on (1-based)."""
    dest: int
    """Floor the passenger wants to get off at (1-based); never equal to ``source``."""

    def __post_init__(self) -> None:
        """Check the request is well formed on its own, without knowing the building.

        Floors are only checked against the bottom (1) here; the top depends on the
        building, so :func:`validate_requests` checks that later.

        Raises:
            ValueError: ``time`` is negative, a floor is below 1, or ``source`` equals
                ``dest``.
        """
        if self.time < 0:
            raise ValueError(f"request {self.id!r}: time must be >= 0, got {self.time}")
        if self.source < 1 or self.dest < 1:
            raise ValueError(f"request {self.id!r}: floors must be >= 1")
        if self.source == self.dest:
            raise ValueError(f"request {self.id!r}: source and dest are both {self.source}")

    @property
    def direction(self) -> Direction:
        """Which way the passenger travels. Never ``IDLE``: source and dest differ."""
        return Direction.toward(self.source, self.dest)


@dataclass(frozen=True)
class BuildingConfig:
    """Building and fleet parameters. Floors are numbered ``1..num_floors``.

    Every car may stop at every floor: the engine knows nothing about express cars. A
    scheduler that wants to keep one car on a subset of floors (the ``express`` built-in)
    imposes that on itself through its assignment rule.
    """

    num_elevators: int
    """Number of cars in the fleet; their ids are ``0..num_elevators - 1``."""
    num_floors: int
    """Number of floors in the building; the top floor is ``num_floors``."""
    capacity: int
    """Most passengers one car can carry at once (same for every car)."""
    stop_time: int = 0
    """Ticks a car is held in place after anyone boards or alights. 0 = stops are free."""

    def __post_init__(self) -> None:
        """Reject a building or fleet the simulation cannot run.

        Raises:
            ValueError: fewer than one elevator, fewer than two floors, capacity below 1,
                or negative ``stop_time``.
        """
        # Basic size checks: a building needs at least one car and two floors to be useful.
        if self.num_elevators < 1:
            raise ValueError("num_elevators must be >= 1")
        if self.num_floors < 2:
            raise ValueError("num_floors must be >= 2")
        if self.capacity < 1:
            raise ValueError("capacity must be >= 1")
        if self.stop_time < 0:
            raise ValueError("stop_time must be >= 0")


@dataclass
class Passenger:
    """A request plus the timestamps the simulation stamps on it as it is served."""

    request: Request
    """The original travel request."""
    elevator_id: int | None = None
    """Car the passenger boarded; ``None`` until they board."""
    pickup_time: int | None = None
    """Tick the passenger boarded; ``None`` while still waiting."""
    dropoff_time: int | None = None
    """Tick the passenger got off at their destination; ``None`` until then."""

    @property
    def wait_time(self) -> int | None:
        """Ticks from request to boarding; ``None`` if not yet picked up."""
        if self.pickup_time is None:
            return None
        return self.pickup_time - self.request.time

    @property
    def travel_time(self) -> int | None:
        """Ticks spent riding, boarding to drop-off; ``None`` until the trip is finished."""
        if self.pickup_time is None or self.dropoff_time is None:
            return None
        return self.dropoff_time - self.pickup_time

    @property
    def total_time(self) -> int | None:
        """Ticks from request to drop-off (wait plus ride); ``None`` until dropped off."""
        if self.dropoff_time is None:
            return None
        return self.dropoff_time - self.request.time

    @property
    def is_waiting(self) -> bool:
        """Whether the passenger has not yet boarded a car."""
        return self.pickup_time is None

    @property
    def is_riding(self) -> bool:
        """Whether the passenger is currently aboard a car."""
        return self.pickup_time is not None and self.dropoff_time is None

    @property
    def is_done(self) -> bool:
        """Whether the passenger has reached their destination."""
        return self.dropoff_time is not None


@dataclass(frozen=True)
class Rider:
    """A passenger aboard a car, as seen by schedulers."""

    request: Request
    """The rider's original travel request (its ``dest`` is where they get off)."""
    pickup_time: int
    """Tick the rider boarded."""


@dataclass(frozen=True)
class ElevatorView:
    """Read-only snapshot of one car handed to schedulers."""

    id: int
    """0-based id of the car."""
    floor: int
    """Floor the car is on right now (1-based)."""
    capacity: int
    """Most riders the car can hold."""
    riders: tuple[Rider, ...]
    """Passengers currently aboard, in boarding order."""
    held_until: int
    """First tick at which the car may move again; equal to the current time when free."""

    @property
    def load(self) -> int:
        """Number of riders currently aboard."""
        return len(self.riders)

    @property
    def free_capacity(self) -> int:
        """Seats left on the car."""
        return self.capacity - self.load

    @property
    def is_full(self) -> bool:
        """Whether the car has no free seats."""
        return self.load >= self.capacity


@dataclass
class Elevator:
    """One car. Engine-internal mutable state; schedulers see :class:`ElevatorView`."""

    id: int
    """0-based id of the car."""
    capacity: int
    """Most riders the car can hold."""
    floor: int = 1
    """Floor the car is on (1-based); every car starts on the ground floor."""
    riders: list[Passenger] = field(default_factory=list)
    """Passengers currently aboard, in boarding order."""
    held_until: int = 0
    """First tick at which the car may move again (see ``BuildingConfig.stop_time``)."""

    @property
    def load(self) -> int:
        """Number of riders currently aboard."""
        return len(self.riders)

    @property
    def free_capacity(self) -> int:
        """Seats left. The engine's boarding limit; mirrors :attr:`ElevatorView.free_capacity`."""
        return self.capacity - self.load

    @property
    def is_full(self) -> bool:
        """Whether the car has no free seats."""
        return self.load >= self.capacity

    def view(self) -> ElevatorView:
        """Return a frozen snapshot of this car that is safe to hand to a scheduler.

        Returns:
            An :class:`ElevatorView` copy; later changes to this car do not affect it.
        """
        # Copy each rider into an immutable Rider so the scheduler cannot touch the
        # engine's live Passenger objects.
        riders = []
        for p in self.riders:
            # Anyone aboard has boarded, so their pickup time is always stamped.
            assert p.pickup_time is not None
            riders.append(Rider(request=p.request, pickup_time=p.pickup_time))
        return ElevatorView(
            id=self.id,
            floor=self.floor,
            capacity=self.capacity,
            riders=tuple(riders),
            held_until=self.held_until,
        )


@dataclass(frozen=True)
class SimulationState:
    """Immutable snapshot of the world handed to the scheduler once per tick."""

    time: int
    """Current tick."""
    config: BuildingConfig
    """Building and fleet parameters for this run."""
    elevators: tuple[ElevatorView, ...]
    """One snapshot per car, indexed by elevator id."""
    waiting: tuple[Request, ...]
    """Every request released but not yet picked up, in request order. Includes ``new``."""
    new: tuple[Request, ...]
    """Requests released this tick."""


def validate_requests(requests: list[Request], config: BuildingConfig) -> None:
    """Raise ``ValueError`` for a request the building cannot serve, before the run starts.

    Two ways it cannot: a floor outside ``1..num_floors``, or a reused id. Both are knowable
    at construction time, so they are raised here rather than left to surface on the tick the
    request is released, which on a late request means most of a run wasted and no output
    files written.

    Args:
        requests: Every request in the run.
        config: The building the requests will run in.

    Raises:
        ValueError: an id appears twice, or a floor is above ``num_floors``.
    """
    seen: set[str] = set()
    for r in requests:
        # Ids key the passenger log and scheduler plans, so they must be unique.
        if r.id in seen:
            raise ValueError(f"request id {r.id!r} appears more than once")
        seen.add(r.id)
        # Request itself only checked floors >= 1; the top of the building is checked here.
        for floor in (r.source, r.dest):
            if not 1 <= floor <= config.num_floors:
                raise ValueError(
                    f"request {r.id!r}: floor {floor} is outside 1..{config.num_floors}"
                )
