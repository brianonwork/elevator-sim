"""Assignment policies: which car takes a newly released request.

A policy sees the snapshot, every car's current assignments and headings, and the
request. It returns a car id. It never mutates its inputs.

Terms used below:

- The **express band** is the lobby (floor 1) plus the top third of the building. The
  ``express`` scheduler keeps one car on trips inside that band and nothing else; the engine
  does not know about it, so the restriction lives here, in assignment, not in physics.
- **ETA** is a predicted drop-off (or pickup) tick, obtained by replaying the car's
  controller forward with :func:`~.controller.simulate`.
- **Marginal cost** is how much a total cost goes *up* if this car also takes the new
  request: cost with the request minus cost without it. Picking the car with the smallest
  marginal cost is picking the car that the request inconveniences least.
- A **zone** is a contiguous band of floors that one car treats as its home turf.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from ..models import Direction, ElevatorView, Request, SimulationState
from .controller import Car, from_view, simulate, starve_limit

Assignments = Mapping[int, Sequence[Request]]
"""Car id -> its assigned, still-waiting pickups in assignment order."""

Headings = Mapping[int, Direction]
"""Car id -> the heading the controller committed to last tick."""


class Policy(Protocol):
    """A policy that assigns a request to a car.

    ``candidates`` narrows the choice to a subset of the fleet, which is how ``ZoneBased``
    and ``Express`` express "choose among these". ``None`` means every car. The snapshot
    always describes the whole fleet, so a policy reading ``state.elevators`` still sees the
    truth.
    """

    def __call__(
        self,
        state: SimulationState,
        assigned: Assignments,
        headings: Headings,
        request: Request,
        candidates: Sequence[ElevatorView] | None = None,
    ) -> int:
        """Choose the car that will serve ``request``.

        Args:
            state: This tick's snapshot of the whole simulation.
            assigned: Each car's assigned, still-waiting pickups. Not mutated.
            headings: Each car's heading committed last tick. Not mutated.
            request: The newly released request to place.
            candidates: Cars to choose among; ``None`` = every car.

        Returns:
            The id of the chosen car.
        """
        ...


def choices(
    state: SimulationState, request: Request, candidates: Sequence[ElevatorView] | None
) -> list[ElevatorView]:
    """Return the cars a policy may pick from: the narrowed set if given, else the whole fleet.

    Args:
        state: This tick's snapshot; its ``elevators`` are the whole fleet, in id order.
        request: The request being placed; unused here, kept so every policy's narrowing
            step reads the same way.
        candidates: A caller-narrowed set of cars; ``None`` = every car.

    Returns:
        A fresh list of cars to choose among; never empty, since a fleet has at least one car
        and callers only narrow to non-empty sets.
    """
    return list(candidates) if candidates is not None else list(state.elevators)


class RoundRobin:
    """The next car in id order after the one chosen last, wrapping around."""

    def __init__(self) -> None:
        """Start before car 0 so the first request goes to the lowest-id eligible car."""
        # -1 is below every real car id (ids start at 0).
        self.last = -1

    def __call__(
        self,
        state: SimulationState,
        assigned: Assignments,
        headings: Headings,
        request: Request,
        candidates: Sequence[ElevatorView] | None = None,
    ) -> int:
        """Return the next car id after self.last, wrapping around.

        Args:
            state: This tick's snapshot.
            assigned: Ignored; round-robin does not look at workload.
            headings: Ignored; round-robin does not look at direction.
            request: The request being placed.
            candidates: Cars to choose among; ``None`` = every car.

        Returns:
            The chosen car id, which is also remembered for the next call (this is the
            one policy that keeps state between calls, in ``self.last``).
        """
        cars = choices(state, request, candidates)
        later = [c for c in cars if c.id > self.last]
        # No car after the last pick: wrap round to the first one.
        self.last = (later or cars)[0].id
        return self.last


def nearest_car(
    state: SimulationState,
    assigned: Assignments,
    headings: Headings,
    request: Request,
    candidates: Sequence[ElevatorView] | None = None,
) -> int:
    """Return the car closest in floors to the request's source.

    Smallest distance to the source; ties to a car heading toward it, then fewer
    passengers committed (riders aboard plus assigned pickups still waiting), then id.

    Args:
        state: This tick's snapshot.
        assigned: Each car's assigned, still-waiting pickups (used for the tie-break).
        headings: Each car's committed heading (used for the tie-break).
        request: The request being placed.
        candidates: Cars to choose among; ``None`` = every eligible car.

    Returns:
        The chosen car id.
    """

    def key(car: ElevatorView) -> tuple[int, int, int, int]:
        """Sort key: distance, then not-heading-toward, then committed load, then id."""
        heading = headings.get(car.id, Direction.IDLE)
        # An idle car is not "heading toward" anything, even if it happens to be nearby.
        toward = heading is not Direction.IDLE and heading is Direction.toward(
            car.floor, request.source
        )
        committed = car.load + len(assigned.get(car.id, ()))
        # 0 sorts before 1, so cars already heading toward the source win ties.
        return (abs(car.floor - request.source), 0 if toward else 1, committed, car.id)

    return min(choices(state, request, candidates), key=key).id


def nearest_car_eta(
    state: SimulationState,
    assigned: Assignments,
    headings: Headings,
    request: Request,
    candidates: Sequence[ElevatorView] | None = None,
) -> int:
    """Return the nearest car measured in ticks: the earliest pickup under the real controller.

    ``nearest_car`` measures floors, so a car just past the rider and sweeping away, or one
    too full to take them, still counts as close. Replaying each car with the request
    appended folds heading, sweep order and capacity into one number. Unlike ``EtaCost`` it
    ignores the rider's own ride and the delay they add to others, and it replays each car
    once rather than twice. Ties go to fewer committed passengers, then lower id (the last
    two of ``nearest_car``'s tie-breaks; there is no heading tie-break here).

    Args:
        state: This tick's snapshot.
        assigned: Each car's assigned, still-waiting pickups.
        headings: Each car's committed heading, used to seed the replay.
        request: The request being placed.
        candidates: Cars to choose among; ``None`` = every eligible car.

    Returns:
        The chosen car id.
    """

    def key(view: ElevatorView) -> tuple[int, int, int]:
        """Sort key: predicted pickup tick, then committed load, then id."""
        car = from_view(
            view,
            headings.get(view.id, Direction.IDLE),
            assigned.get(view.id, ()),
            state.time,
            starve_after=starve_limit(state.config),
        )
        # Pretend this car took the request, then replay to see when it would board.
        car.pickups.append(request)
        picks: dict[str, int] = {}
        simulate(car, state.time, state.config.stop_time, boarded_at=picks)
        committed = view.load + len(assigned.get(view.id, ()))
        return (picks[request.id], committed, view.id)

    return min(choices(state, request, candidates), key=key).id


class EtaCost:
    """Smallest increase in ``Σ (dropoff - request_time) ** power`` from a replayed sweep.

    For each car, predict every passenger's total journey time (request to drop-off) with and
    without the new request, and give it to the car where the sum grows least. The choice is greedy:
    made once per request, never revisited, and blind to future requests. ``power`` 1 counts every
    tick equally; ``power`` 2 punishes long journeys more heavily, trading a little average time for
    fewer very long waits ("fair").
    """

    def __init__(self, power: float = 1.0) -> None:
        """Store the exponent applied to each passenger's journey time.

        Args:
            power: Exponent on each ``dropoff - request_time`` (ticks). 1.0 = total time;
                larger values weigh long journeys more.
        """
        self.power = power

    def __call__(
        self,
        state: SimulationState,
        assigned: Assignments,
        headings: Headings,
        request: Request,
        candidates: Sequence[ElevatorView] | None = None,
    ) -> int:
        """Return the car id with the smallest marginal cost for this request.

        Args:
            state: This tick's snapshot.
            assigned: Each car's assigned, still-waiting pickups.
            headings: Each car's committed heading, used to seed the replay.
            request: The request being placed.
            candidates: Cars to choose among; ``None`` = every car.

        Returns:
            The chosen car id. Ties go to the car with fewer riders aboard, then lower id.
        """

        def key(view: ElevatorView) -> tuple[float, int, int]:
            """Sort key: marginal cost, then riders aboard, then id."""
            car = from_view(
                view,
                headings.get(view.id, Direction.IDLE),
                assigned.get(view.id, ()),
                state.time,
                starve_after=starve_limit(state.config),
            )
            return (self.cost(car, request, state.time, state.config.stop_time), view.load, view.id)

        return min(choices(state, request, candidates), key=key).id

    def cost(self, car: Car, request: Request, now: int, stop_time: int) -> float:
        """Return the marginal cost of appending ``request`` to ``car``'s pickups.

        Args:
            car: The car as it stands, without the new request. Not mutated.
            request: The request being placed.
            now: The current tick; the replays start here.
            stop_time: Ticks a car is held after anyone boards or alights.

        Returns:
            Cost with the request minus cost without it, where cost is the sum over
            passengers of ``(dropoff - request_time) ** power``, with journey time measured
            in ticks.
        """
        # Request time of everyone the car is responsible for, including the new request.
        times = {r.id: r.time for r in car.riders + car.pickups}
        times[request.id] = request.time
        # Two replays: the car's plan as-is, and the same plan with the request appended.
        without = simulate(car, now, stop_time)
        with_request = car.copy()
        with_request.pickups.append(request)
        with_ = simulate(with_request, now, stop_time)

        # Sum per-passenger differences rather than subtracting two totals: for a
        # non-integer power the totals are inexact floats, and their rounding residue would
        # otherwise decide ties that the documented tie-break should. A passenger whose ETA
        # is unchanged contributes exactly zero.
        cost = 0.0
        for i, eta in with_.items():
            before = without.get(i)
            if before == eta:
                continue
            cost += (eta - times[i]) ** self.power
            if before is not None:
                cost -= (before - times[i]) ** self.power
        return cost


def zones(num_cars: int, num_floors: int) -> tuple[tuple[int, int], ...]:
    """Return one contiguous, non-empty floor range per car, together covering the building.

    Ranges are near-equal and disjoint while there are at least as many floors as cars. With
    more cars than floors disjointness is impossible, so the surplus cars share: car ``i``
    takes the single floor ``i % num_floors + 1``. Every car still gets a real floor, which is
    what the factory's park centres depend on -- an empty range would centre on floor 0 and
    the engine would reject the plan on the first tick.

    Args:
        num_cars: Number of cars in the fleet.
        num_floors: Number of floors in the building.

    Returns:
        A tuple indexed by car id (0-based) of inclusive ``(lowest, highest)`` 1-based
        floor pairs.
    """
    # More cars than floors: every car gets one floor, cycling through the building.
    if num_cars > num_floors:
        return tuple((i % num_floors + 1, i % num_floors + 1) for i in range(num_cars))
    # Split floors 1..num_floors at num_cars + 1 evenly spaced edges; car i owns
    # [edges[i], edges[i + 1] - 1]. Integer division keeps sizes within one of each other.
    edges = [1 + num_floors * i // num_cars for i in range(num_cars + 1)]
    return tuple((edges[i], edges[i + 1] - 1) for i in range(num_cars))


class ZoneBased:
    """Prefer the car whose home zone holds the source; fall back when it is over-committed.

    A zone is a bias, not a fence: unlike the ``Express`` car, a zone car can be passed over,
    and any car may serve any floor. The other half of zoning is that idle cars park in
    their zone so the fleet stays spread out instead of bunching where the calls were;
    that is wired up by the factory, through ``DestinationDispatch(park=...)``.
    """

    def __init__(self, inner: Policy = nearest_car, load_factor: float = 1.5) -> None:
        """Configure the fallback policy and the over-commitment threshold.

        ``inner`` breaks ties within the zone. A car is over-committed once it holds
        ``load_factor`` times its capacity in riders plus assigned pickups.

        Args:
            inner: Policy used to choose among home-zone cars, or among every car when
                no home-zone car is available.
            load_factor: Multiple of capacity at which a home car stops getting preference.
                The default 1.5 lets a home car be committed to half a load more than it
                can seat at once (riders drop off along the way, freeing seats). If no
                eligible car has the source in its zone and is under this threshold, the
                request goes to ``inner`` over every eligible car: ``candidates`` when the
                caller narrowed the choice, the whole fleet otherwise.
        """
        self.inner = inner
        self.load_factor = load_factor

    def __call__(
        self,
        state: SimulationState,
        assigned: Assignments,
        headings: Headings,
        request: Request,
        candidates: Sequence[ElevatorView] | None = None,
    ) -> int:
        """Return the zone car for the source, or fall back to ``inner`` over the candidates.

        Args:
            state: This tick's snapshot; its config gives the fleet and building size.
            assigned: Each car's assigned, still-waiting pickups.
            headings: Each car's committed heading, passed through to ``inner``.
            request: The request being placed.
            candidates: Cars to choose among; ``None`` = every car.

        Returns:
            The chosen car id.
        """
        zone = zones(state.config.num_elevators, state.config.num_floors)
        # Home cars: the source floor is inside their zone and they are not over-committed.
        home = [
            car
            for car in choices(state, request, candidates)
            if zone[car.id][0] <= request.source <= zone[car.id][1]
            and car.load + len(assigned.get(car.id, ())) < self.load_factor * car.capacity
        ]
        # No home car available: ``inner`` chooses among the cars the caller allowed (None =
        # every car). Widening to the whole fleet here would undo an outer policy's narrowing.
        return self.inner(state, assigned, headings, request, home or candidates)


def top_band(num_floors: int) -> tuple[int, int]:
    """Return the top third of the building: floors 41 to 60 in a 60-floor building.

    The rule the ``express`` scheduler and the trials' preset 8 share, so both mean the same
    floors by "the top band".

    Args:
        num_floors: Number of floors in the building.

    Returns:
        ``(lowest, highest)`` floor of the band, both 1-based and inclusive.
    """
    return num_floors * 2 // 3 + 1, num_floors


def express_floors(num_floors: int) -> frozenset[int]:
    """Return the express band: the lobby plus :func:`top_band`.

    Args:
        num_floors: Number of floors in the building.

    Returns:
        Floor 1 and every floor of the top third, as a set of 1-based floors.
    """
    lo, hi = top_band(num_floors)
    return frozenset({1, *range(lo, hi + 1)})


class Express:
    """Split the fleet: express cars own every trip inside the express band, locals the rest.

    Some cars are express cars. Every request whose source and destination both lie in the express
    band (lobby plus top third) goes to an express car and to nothing else; every other request goes
    to a local car and to nothing else. So an express car never stops on a middle floor, and a local
    car never carries a lobby-to-top rider. Within each side, ``inner`` picks the car -- with two
    express cars, by the same marginal cost the locals use.

    This is a restriction the scheduler imposes on itself. The engine lets every car stop
    anywhere; nothing outside this policy stops an express car being sent to floor 20 -- it
    simply is never assigned anyone who wants to go there. The fleet must hold at least one
    car of each kind, which ``build_express`` guarantees. A pure function of the snapshot: it
    never mutates its inputs.
    """

    def __init__(self, inner: Policy, express_cars: frozenset[int], floors: frozenset[int]) -> None:
        """Configure which cars are express and which floors they serve.

        Args:
            inner: Policy that chooses among the cars this one allows.
            express_cars: 0-based ids of the express cars; every other car is local.
            floors: The floors the express cars serve (1-based); a request is an express
                trip when both its ends are in this set.
        """
        self.inner = inner
        self.express_cars = express_cars
        self.floors = floors

    def __call__(
        self,
        state: SimulationState,
        assigned: Assignments,
        headings: Headings,
        request: Request,
        candidates: Sequence[ElevatorView] | None = None,
    ) -> int:
        """Return ``inner``'s pick, from the side of the fleet that owns this request.

        Args:
            state: This tick's snapshot.
            assigned: Each car's assigned, still-waiting pickups, passed through to ``inner``.
            headings: Each car's committed heading, passed through to ``inner``.
            request: The request being placed.
            candidates: Cars to choose among; ``None`` = every car. Narrowed further to
                the express cars for an express trip and the local cars otherwise.

        Returns:
            The chosen car id.

        Raises:
            ValueError: ``candidates`` holds no car on the side that owns this trip.
        """
        is_express_trip = request.source in self.floors and request.dest in self.floors
        # Keep exactly the side that owns this trip: express cars for a band trip, locals
        # for everything else.
        allowed = [
            car
            for car in choices(state, request, candidates)
            if (car.id in self.express_cars) == is_express_trip
        ]
        # Only a caller's narrowing can empty a side; say so rather than let ``inner`` fail
        # on an empty list.
        if not allowed:
            side = "express" if is_express_trip else "local"
            raise ValueError(
                f"request {request.id!r}: no {side} car among the candidates to take it"
            )
        return self.inner(state, assigned, headings, request, allowed)
