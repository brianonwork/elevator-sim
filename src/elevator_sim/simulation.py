"""Simulation engine: the discrete-time tick loop.

The engine owns physics and constraints only: releasing requests at their request time
(never peeking ahead), alighting riders at their destination, applying the scheduler's
boarding and movement plan after validating it, holding cars for ``stop_time`` after a
stop, moving one floor per tick, and logging. It makes no scheduling decision of its own.

Tick order at time ``t``:

1. Log every elevator's floor for time ``t``.
2. Release requests with ``time == t`` into the waiting pool.
3. At each elevator, riders whose destination is the current floor get off. If anyone
   did, the car is held until ``t + stop_time``.
4. Build an immutable :class:`SimulationState` and ask the scheduler for one plan.
5. Validate the whole plan (capacity, location) before touching any car,
   then apply boarding. If anyone boarded, the car is held until ``t + stop_time``.
6. Move each car one floor toward its target, unless it is held.
7. ``t`` advances by one.

All cars start on floor 1, idle.

Time moves in whole steps called **ticks**. In one tick a car can move one floor. Each tick the
engine lets new passengers appear, lets riders off, asks the scheduler what to do, checks that
answer is legal, then applies it. A car that has just stopped to let people on or off is **held**
(cannot move) for ``stop_time`` ticks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .models import (
    BuildingConfig,
    Direction,
    Elevator,
    Passenger,
    Request,
    SimulationState,
    validate_requests,
)
from .scheduler import CarAction, Scheduler, SchedulingError

PositionsLog = list[tuple[int, tuple[int, ...]]]
"""``(time, floors)`` pairs, one per tick, floors ordered by elevator id."""


class SimulationStalled(Exception):
    """Raised by :meth:`Simulation.run` when ``max_ticks`` elapse with work outstanding."""


@dataclass(frozen=True)
class SimulationResult:
    """Everything a finished (or cut-short) run produced."""

    passengers: list[Passenger]
    """Every released passenger with their timestamps, in release order."""
    positions_log: PositionsLog
    """Floor of every car at every tick."""
    ticks: int
    """Number of ticks simulated; the log covers times ``0 .. ticks - 1``."""


class Simulation:
    """One run of the elevator world, driven tick by tick by a scheduler.

    Create it, then call :meth:`run` (or :meth:`step` repeatedly to watch it tick by tick).
    """

    def __init__(
        self, config: BuildingConfig, scheduler: Scheduler, requests: Iterable[Request]
    ):
        """Set up the building with every car on floor 1 and nobody released yet.

        Args:
            config: Building and fleet parameters.
            scheduler: Decides, each tick, who boards which car and where each car goes.
            requests: Every passenger request for the run, in any order.

        Raises:
            ValueError: a request cannot be served by this building (see
                :func:`~elevator_sim.models.validate_requests`).
        """
        self.config = config
        self.scheduler = scheduler
        # Python's sort is stable, so requests sharing a tick keep their input order.
        self._requests = sorted(requests, key=lambda r: r.time)
        validate_requests(self._requests, config)
        # Index of the next not-yet-released request in the sorted list.
        self._next_request = 0
        self.time = 0
        self.elevators: tuple[Elevator, ...] = tuple(
            Elevator(id=i, capacity=config.capacity) for i in range(config.num_elevators)
        )
        self.passengers: list[Passenger] = []
        self._waiting: list[Passenger] = []
        """Released but not yet aboard, in request order."""
        self._unserved = 0
        """Released passengers not yet dropped off. Keeps ``is_done`` O(1)."""
        # Requests released during the current tick; reset to empty at the end of each step.
        self._released_now: tuple[Request, ...] = ()
        self.positions_log: PositionsLog = []

    # -- observation -----------------------------------------------------------------

    @property
    def state(self) -> SimulationState:
        """Immutable snapshot of the world as the scheduler sees it."""
        return SimulationState(
            time=self.time,
            config=self.config,
            elevators=tuple(e.view() for e in self.elevators),
            waiting=tuple(p.request for p in self._waiting),
            new=self._released_now,
        )

    @property
    def waiting(self) -> int:
        """Released passengers not yet aboard. A pool that runs away means overload."""
        return len(self._waiting)

    @property
    def is_done(self) -> bool:
        """True once every request has been released and every passenger dropped off."""
        return self._next_request == len(self._requests) and self._unserved == 0

    # -- driving -----------------------------------------------------------------------

    def step(self) -> None:
        """Advance the simulation by exactly one tick.

        Follows the tick order in the module docstring (log, release, alight, plan, board,
        move, advance).

        Raises:
            SchedulingError: the scheduler raised it, or its plan breaks an engine rule. No
                boarding or movement happens, but the log, release and alight phases have
                already run and ``time`` has not advanced, so do not step again.
        """
        self._log_positions()
        self._release_requests()
        for elevator in self.elevators:
            # An expired hold is pulled up to "now", so held_until == time means "free".
            if elevator.held_until < self.time:
                elevator.held_until = self.time
            self._alight(elevator)
        # Ask the scheduler once for the whole fleet, then board and move from that plan.
        plan = self.scheduler.step(self.state)
        self._board(plan)
        for elevator in self.elevators:
            # A car missing from the plan stays put.
            action = plan.get(elevator.id)
            self._move(elevator, None if action is None else action.target)
        self._released_now = ()
        self.time += 1

    def run(self, max_ticks: int | None = None, until: int | None = None) -> SimulationResult:
        """Tick until all passengers are served. Always logs at least time 0.

        ``until=N`` stops after tick ``N - 1`` and returns normally, leaving whoever is
        still waiting or riding unstamped, for a caller that wants a fixed-length window
        rather than a full run. ``until=0`` still runs tick 0, because
        the positions log is required to start at 0. ``max_ticks`` is the runaway guard
        and still raises :class:`SimulationStalled`.

        Args:
            max_ticks: Safety limit in ticks; reaching it before every request has been
                released and delivered is an error. ``None`` = no limit.
            until: Stop cleanly once this many ticks have run, even with work left.
                ``None`` = run until everyone is served.

        Returns:
            The passengers, the positions log and the number of ticks simulated.

        Raises:
            SimulationStalled: the clock reached ``max_ticks`` before every request was
                released and delivered (including requests timed at or after
                ``max_ticks``, which the message counts). ``max_ticks=0`` always raises.
        """
        # The ``time == 0`` clause forces at least one tick, even with no requests at all.
        while not self.is_done or self.time == 0:
            # Planned cut-off (throughput runs); never before tick 0 has been logged.
            if until is not None and self.time >= until and self.time > 0:
                break
            # Runaway guard: most likely a scheduler that never serves someone.
            if max_ticks is not None and self.time >= max_ticks:
                unserved = [p.request.id for p in self.passengers if not p.is_done]
                message = (
                    f"{max_ticks} ticks elapsed with {len(unserved)} passenger(s) unserved: "
                    f"{unserved}"
                )
                # Requests timed at or after max_ticks never got the chance; say so, or
                # the message reads "0 unserved" with no visible cause.
                unreleased = len(self._requests) - self._next_request
                if unreleased:
                    message += (
                        f"; {unreleased} request(s) not yet released (next at tick "
                        f"{self._requests[self._next_request].time})"
                    )
                raise SimulationStalled(message)
            self.step()
        return SimulationResult(
            passengers=list(self.passengers),
            positions_log=list(self.positions_log),
            ticks=self.time,
        )

    # -- tick phases -----------------------------------------------------------------

    def _log_positions(self) -> None:
        """Record every car's floor for the current tick (tick phase 1)."""
        self.positions_log.append((self.time, tuple(e.floor for e in self.elevators)))

    def _release_requests(self) -> None:
        """Move requests whose time has come into the waiting pool (tick phase 2).

        Requests are released only when their time is reached, so schedulers never see the
        future.
        """
        released: list[Request] = []
        # Requests are sorted by time, so walk forward until the next one is in the future.
        while (
            self._next_request < len(self._requests)
            and self._requests[self._next_request].time <= self.time
        ):
            request = self._requests[self._next_request]
            self._next_request += 1
            passenger = Passenger(request=request)
            self.passengers.append(passenger)
            self._waiting.append(passenger)
            self._unserved += 1
            released.append(request)
        self._released_now = tuple(released)

    def _alight(self, elevator: Elevator) -> None:
        """Let off every rider whose destination is the car's current floor (tick phase 3).

        Args:
            elevator: The car to unload. Held for ``stop_time`` if anyone gets off.
        """
        staying: list[Passenger] = []
        stopped = False
        for rider in elevator.riders:
            # Arrived: stamp the drop-off time and count one fewer passenger outstanding.
            if rider.request.dest == elevator.floor:
                rider.dropoff_time = self.time
                self._unserved -= 1
                stopped = True
            else:
                staying.append(rider)
        elevator.riders = staying
        if stopped:
            self._hold(elevator)

    def _resolve_plan(
        self, plan: Mapping[int, CarAction]
    ) -> list[tuple[Elevator, list[Passenger]]]:
        """Validate the whole plan and resolve it to boardings. Mutates nothing.

        Every constraint the engine enforces is checked here, before any car is touched, so
        a plan that fails partway leaves the world exactly as it was.

        Args:
            plan: The scheduler's actions, keyed by elevator id.

        Returns:
            One ``(car, passengers to board in order)`` pair per car named in the plan.

        Raises:
            SchedulingError: the plan names an unknown car, a target floor outside the
                building, a passenger who is not waiting, is on another floor, is boarded
                twice, or more passengers than seats.
        """
        waiting_by_id = {p.request.id: p for p in self._waiting}
        # Passenger id -> car it was put on this tick, to catch one passenger on two cars.
        claimed: dict[str, int] = {}
        resolved: list[tuple[Elevator, list[Passenger]]] = []
        for elevator_id, action in plan.items():
            elevator = self._elevator(elevator_id)
            # The target is range-checked even if the car is held and will not move.
            if action.target is not None and not 1 <= action.target <= self.config.num_floors:
                raise SchedulingError(
                    f"scheduler sent elevator {elevator_id} toward floor {action.target}; "
                    f"building has floors 1..{self.config.num_floors}"
                )
            admitted: list[Passenger] = []
            # Check each boarding in turn: not already claimed, actually waiting, on this
            # floor, and a seat free.
            for passenger_id in action.board:
                if passenger_id in claimed:
                    raise SchedulingError(
                        f"passenger {passenger_id!r} boards elevator {elevator_id} but was "
                        f"already boarded onto elevator {claimed[passenger_id]} this tick"
                    )
                passenger = waiting_by_id.get(passenger_id)
                if passenger is None:
                    raise SchedulingError(
                        f"no waiting passenger {passenger_id!r} (elevator {elevator_id})"
                    )
                request = passenger.request
                # Passengers can only board the car on the floor where they are waiting.
                if request.source != elevator.floor:
                    raise SchedulingError(
                        f"passenger {passenger_id!r} is waiting on floor {request.source}; "
                        f"elevator {elevator_id} is on floor {elevator.floor}"
                    )
                # Seats already taken by riders plus those admitted so far this tick.
                if len(admitted) >= elevator.free_capacity:
                    raise SchedulingError(
                        f"elevator {elevator_id} is at capacity ({elevator.capacity}); "
                        f"cannot board passenger {passenger_id!r}"
                    )
                claimed[passenger_id] = elevator_id
                admitted.append(passenger)
            resolved.append((elevator, admitted))
        return resolved

    def _board(self, plan: Mapping[int, CarAction]) -> None:
        """Validate the plan, then put the admitted passengers aboard (tick phase 5).

        Args:
            plan: The scheduler's actions, keyed by elevator id.

        Raises:
            SchedulingError: the plan breaks an engine rule (see :meth:`_resolve_plan`);
                raised before any passenger is moved.
        """
        # Validate everything first so a bad plan changes nothing.
        boardings = self._resolve_plan(plan)
        boarded: set[str] = set()
        for elevator, admitted in boardings:
            for passenger in admitted:
                passenger.pickup_time = self.time
                passenger.elevator_id = elevator.id
                elevator.riders.append(passenger)
                boarded.add(passenger.request.id)
            # Anyone boarding makes this a stop, so the car is held.
            if admitted:
                self._hold(elevator)
        # Remove the boarded passengers from the waiting pool, keeping request order.
        if boarded:
            self._waiting = [p for p in self._waiting if p.request.id not in boarded]

    def _move(self, elevator: Elevator, target: int | None) -> None:
        """Move a car one floor toward its target (tick phase 6).

        Args:
            elevator: The car to move.
            target: Floor to head toward (1-based); ``None`` = stay put.
        """
        # A held car, a car with no target, or a car already there does not move.
        if self.time < elevator.held_until or target is None or target == elevator.floor:
            return
        elevator.floor += int(Direction.toward(elevator.floor, target))

    # -- helpers -----------------------------------------------------------------------

    def _elevator(self, elevator_id: object) -> Elevator:
        """Return the car a plan refers to, checking the id is real.

        Args:
            elevator_id: Key from the scheduler's plan; typed ``object`` because a buggy
                scheduler could use anything as a key.

        Returns:
            The engine's live car with that id.

        Raises:
            SchedulingError: the id is not an int in ``0..num_elevators - 1``.
        """
        if isinstance(elevator_id, int) and 0 <= elevator_id < len(self.elevators):
            return self.elevators[elevator_id]
        raise SchedulingError(
            f"plan names elevator {elevator_id!r}; valid ids are 0..{len(self.elevators) - 1}"
        )

    def _hold(self, elevator: Elevator) -> None:
        """Keep a car in place for ``stop_time`` ticks after a stop.

        ``max`` keeps any longer hold already in force (alighting and boarding in the same
        tick hold the car once, not twice).

        Args:
            elevator: The car that just stopped.
        """
        elevator.held_until = max(elevator.held_until, self.time + self.config.stop_time)
