"""The car controller: LOOK with direction commitment, plus a replay used for ETAs.

This module decides, for one car at a time, which way it goes, who gets on, and which floor it heads
for next. Terms used below:

- **LOOK** is the classic elevator sweep: keep going the same way while there is still a
  stop ahead, then turn round. (It "looks" ahead and turns at the last stop rather than
  running to the end of the shaft.)
- **Direction commitment** means the car remembers the direction it chose last tick (its
  *heading*) and keeps it until the rules below say to change, instead of re-deciding from
  scratch every tick. Riders only board if they travel the committed way.
- **ETA** (estimated time of arrival) is the tick at which a passenger is predicted to be
  dropped off. :func:`simulate` produces these by fast-forwarding a copy of the car.
- A rider is **starved** once they have waited ``starve_after`` ticks without boarding. LOOK
  alone cannot rule this out: seats go in assignment order only among riders on the same
  floor, so floors earlier in the sweep can refill a car every time it passes the rider's
  floor, for as long as new riders keep arriving there. When a car's oldest waiting rider is
  starved, the car **reserves a seat** for them: it boards everyone else only while a seat
  stays free. The riders already aboard get off within one sweep, the car then never fills,
  and LOOK brings it past the rider's floor in the rider's direction within two sweeps. So
  every wait is bounded, whichever policy assigned the rider.

Pure logic over :class:`Car`, a small mutable picture of one car. ``DestinationDispatch``
builds a ``Car`` from the engine's ``ElevatorView`` each tick; :func:`simulate` steps one
forward in time with the same rules to predict drop-off ticks for the cost function.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..models import BuildingConfig, Direction, ElevatorView, Request

# Short aliases: the rules below compare headings constantly and read better without the
# ``Direction.`` prefix.
UP, DOWN, IDLE = Direction.UP, Direction.DOWN, Direction.IDLE

# A round trip is about 2 x floors ticks at stop time 0. Six of them sat above every wait
# eta-cost produced in the trials before the rule existed, so it only fires on a rider the
# sweep is actually passing over, not on ordinary queueing.
STARVE_ROUND_TRIPS = 6


def starve_limit(config: BuildingConfig) -> int:
    """Return the wait after which a car reserves a seat for its oldest rider.

    A pure function of the building, so the live car and every policy's replay derive the
    same threshold from the same config and the ETAs stay exact.

    Args:
        config: The run's configuration; supplies the floor count.

    Returns:
        ``STARVE_ROUND_TRIPS`` round trips of ``2 * num_floors`` ticks each.
    """
    return STARVE_ROUND_TRIPS * 2 * config.num_floors


@dataclass
class Car:
    """The controller's picture of one car.

    Mutable so simulate() can step it. It holds only what the controller rules need: floor,
    riders and hold come from the engine's ``ElevatorView`` snapshot, while heading and
    pickups come from the scheduler (``DestinationDispatch``). It is a throwaway working copy.
    """
    floor: int
    """Floor the car is on now (1-based)."""
    heading: Direction
    """Direction the controller committed to last tick; ``IDLE`` if none."""
    capacity: int
    """Most riders the car can hold at once."""
    riders: list[Request] = field(default_factory=list)
    """Requests aboard."""
    pickups: list[Request] = field(default_factory=list)
    """Assigned and still waiting, in assignment order."""
    held: int = 0
    """Ticks the car must stay put before it may move (0 = free now)."""
    park: int | None = None
    """Floor to drift back to with nothing committed anywhere. ``None`` = stay put."""
    starve_after: int | None = None
    """Ticks the oldest pickup may wait before a seat is kept for it. ``None`` = never."""

    @property
    def free_capacity(self) -> int:
        """Seats left for boarding."""
        return self.capacity - len(self.riders)

    def copy(self) -> Car:
        """Return a deep-enough copy safe for simulate() to mutate independently.

        The rider and pickup lists are copied; the ``Request`` objects inside are shared,
        which is safe because requests are frozen.

        Returns:
            A new ``Car`` with the same field values and its own lists.
        """
        return Car(self.floor, self.heading, self.capacity, list(self.riders),
                   list(self.pickups), self.held, self.park, self.starve_after)


def from_view(
    view: ElevatorView, heading: Direction, pickups: Sequence[Request], now: int,
    park: int | None = None, starve_after: int | None = None,
) -> Car:
    """Build the controller's picture of ``view`` with the scheduler's own heading and pickups.

    The engine knows where the car is and who is aboard; only the scheduler knows the
    committed heading and which waiting passengers were assigned to this car, so both
    halves are combined here.

    Args:
        view: The engine's read-only snapshot of the car.
        heading: Heading the scheduler committed to last tick (``IDLE`` if none).
        pickups: Requests assigned to this car and still waiting, in assignment order.
        now: The current tick, used to turn the view's absolute ``held_until`` tick into a
            count of ticks still to wait.
        park: Floor the car drifts back to when idle; ``None`` = stay put.
        starve_after: Ticks the oldest pickup may wait before a seat is kept for it (see
            :func:`starve_limit`); ``None`` = never.

    Returns:
        A fresh ``Car`` the caller may mutate freely.
    """
    return Car(
        floor=view.floor,
        heading=heading,
        capacity=view.capacity,
        riders=[r.request for r in view.riders],
        pickups=list(pickups),
        # held_until is an absolute tick (the engine sets it to now when the car is free);
        # max(0, ...) only guards against a stale or hand-built view.
        held=max(0, view.held_until - now),
        park=park,
        starve_after=starve_after,
    )


def stops(car: Car) -> set[int]:
    """Return the floors the car must still visit, excluding the one it is on.

    A stop is either a rider's destination or an assigned pickup's source floor.

    Args:
        car: The car to inspect.

    Returns:
        The set of 1-based floors still to visit; empty when there is nothing to do
        elsewhere.
    """
    pending = {r.dest for r in car.riders} | {p.source for p in car.pickups}
    # The current floor is handled by alighting/boarding this tick, not by moving.
    pending.discard(car.floor)
    return pending


def waiting_here(car: Car) -> list[Request]:
    """Return the assigned pickups standing on the car's current floor, in assignment order.

    Args:
        car: The car to inspect.

    Returns:
        The pickups whose source is ``car.floor``; empty if none.
    """
    return [p for p in car.pickups if p.source == car.floor]


def overdue(car: Car, now: int) -> Request | None:
    """Return the car's oldest waiting pickup if it is starved, else ``None``.

    Pickups are kept in assignment order, which is release order, so the first is the
    oldest.

    Args:
        car: The car to inspect.
        now: The current tick.

    Returns:
        The first pickup when it has waited at least ``car.starve_after`` ticks; ``None``
        when the car has no pickups, no threshold, or its oldest rider is within it.
    """
    if car.starve_after is None or not car.pickups:
        return None
    oldest = car.pickups[0]
    return oldest if now - oldest.time >= car.starve_after else None


def _ahead(floor: int, heading: Direction, floors: set[int]) -> set[int]:
    """Return the floors that lie strictly ahead of ``floor`` when travelling ``heading``.

    Args:
        floor: The car's current floor (1-based).
        heading: Direction of travel; ``IDLE`` has nothing ahead.
        floors: Candidate floors to filter.

    Returns:
        The subset of ``floors`` above ``floor`` for ``UP``, below it for ``DOWN``, and an
        empty set for ``IDLE``.
    """
    if heading is UP:
        return {f for f in floors if f > floor}
    if heading is DOWN:
        return {f for f in floors if f < floor}
    return set()


def next_heading(
    car: Car, pending: set[int] | None = None, here: Sequence[Request] | None = None,
    now: int = 0,
) -> Direction:
    """Return the direction the car commits to this tick.

    In short: keep going the way you were going while there is work ahead, turn round when
    there isn't, and when idle go to the nearest job. The exact rules, in priority order:

    First matching rule wins: (1) riders aboard and no heading yet -- head toward the first
    rider's destination; (2) heading set with a stop ahead -- keep it; (3) heading set,
    nothing ahead, but a stop behind and a pickup at this floor that would board and continue
    that way -- keep it, extending the sweep before turning back; (4) heading set with stops
    only behind -- reverse; (5) heading idle with a stop elsewhere -- head for the nearest
    one, lower floor on ties; (6) nothing elsewhere but a pickup at this floor -- follow the
    earliest-assigned one's travel direction; (7) otherwise idle.

    A loaded car never reverses away from its riders, because every rider destination is a
    stop and rule 2 keeps the heading while any of them lies ahead. Rule 1 does not need to
    say so, and must not: if no rider destination lies ahead -- a state ``boarding`` makes
    unreachable, since it admits only riders travelling the committed way -- then holding the
    heading returns no target and the car stands still for good, re-committing the same
    heading every tick. Falling through to rule 4 reverses and delivers them instead.

    Rule 3 asks whether a pickup here *would board*, not merely whether one is going the
    car's way. The two differ when the car's only free seat is kept for a starved rider
    elsewhere (:func:`boarding`): nobody boards, so no stop appears ahead, and keeping the
    heading would leave the car standing here for good. Rule 4 then turns it round toward
    the starved rider instead.

    ``pending`` and ``here`` are ``stops(car)`` and ``waiting_here(car)``; ``plan_car``
    passes them in so the replay's inner loop derives them once instead of three times.

    Args:
        car: The car to decide for. Not mutated.
        pending: Precomputed ``stops(car)``; ``None`` = compute it here.
        here: Precomputed ``waiting_here(car)``; ``None`` = compute it here.
        now: The current tick, which decides whether a seat is being kept for a starved
            rider and so whether a pickup here would board.

    Returns:
        ``UP``, ``DOWN`` or ``IDLE`` (nothing to do anywhere).
    """
    # Rule 1: riders aboard but no heading yet.
    if car.riders and car.heading is IDLE:
        return UP if car.riders[0].dest > car.floor else DOWN
    pending = stops(car) if pending is None else pending
    here = waiting_here(car) if here is None else here
    if car.heading is not IDLE:
        # Rule 2: a stop still lies ahead, so keep sweeping.
        if _ahead(car.floor, car.heading, pending):
            return car.heading
        # Rule 3: nothing ahead, but someone here will board and keep going our way.
        if pending and boarding(car, car.heading, here, now):
            return car.heading
        # Rule 4: stops only behind, so reverse (Direction is +1/-1, so negation flips it).
        if pending:
            return Direction(-car.heading)
    elif pending:
        # Rule 5: idle with work elsewhere; go to the nearest stop, lower floor on ties.
        nearest = min(pending, key=lambda f: (abs(f - car.floor), f))
        return Direction.toward(car.floor, nearest)
    # Rule 6: the only work is a pickup on this floor; take the first one's direction.
    if here:
        return here[0].direction
    # Rule 7: nothing to do anywhere.
    return IDLE


def boarding(
    car: Car, heading: Direction, here: Sequence[Request] | None = None, now: int = 0,
) -> list[Request]:
    """Return the pickups at this floor travelling in ``heading``, in order, up to free capacity.

    While the car's oldest pickup is starved (:func:`overdue`) and not boarding here, one
    seat is kept empty for them. That is what bounds every wait: see the module docstring.

    Args:
        car: The car doing the boarding. Not mutated.
        heading: The heading just committed to; only riders going this way may board.
        here: Precomputed ``waiting_here(car)``; ``None`` = compute it here.
        now: The current tick, used to tell whether the oldest pickup is starved.

    Returns:
        The requests that board this tick, earliest-assigned first; empty when idle, full,
        or nobody here is travelling ``heading``.
    """
    # An idle car has no committed direction, so nobody can be admitted.
    if heading is IDLE:
        return []
    here = waiting_here(car) if here is None else here
    ready = [p for p in here if p.direction is heading]
    seats = car.free_capacity
    # A starved rider elsewhere keeps a seat. One boarding here is the oldest pickup, so it
    # is first in ``ready`` and the slice below admits it.
    starved = overdue(car, now)
    if starved is not None and starved not in ready:
        seats -= 1
    # max(0, ...) guards against a negative slice bound if the car is somehow over capacity.
    return ready[: max(0, seats)]


def next_target(
    car: Car, heading: Direction, boarded: Sequence[Request],
    pending: set[int] | None = None,
) -> int | None:
    """Return the nearest stop in ``heading`` once ``boarded`` passengers' destinations count.

    An idle heading means nothing is committed anywhere, so a car with a park floor
    drifts back to it. Without one -- every scheduler but ``zone-based`` -- it stays put.

    Args:
        car: The car to plan for. Not mutated.
        heading: The heading committed to this tick.
        boarded: Requests boarding this tick; their destinations become stops too.
        pending: Precomputed ``stops(car)``; ``None`` = compute it here.

    Returns:
        The 1-based floor to move toward, or ``None`` to stay put.
    """
    if heading is IDLE:
        return car.park
    pending = stops(car) if pending is None else pending
    ahead = _ahead(car.floor, heading, pending | {p.dest for p in boarded})
    # Nothing ahead in the committed direction: stay here this tick.
    if not ahead:
        return None
    # "Nearest ahead" is the lowest floor above when going up, the highest below going down.
    return min(ahead) if heading is UP else max(ahead)


def plan_car(car: Car, now: int) -> tuple[Direction, list[Request], int | None]:
    """Return the heading, boarding list, and next target for this tick in one call.

    Nothing mutates ``car`` between the three steps, so the stop set and the at-this-floor
    pickups are derived once here rather than independently in each. This is the inner
    loop of :func:`simulate`, which is where the cost schedulers spend their time.

    Args:
        car: The car to plan for. Not mutated.
        now: The current tick, which decides whether the oldest pickup is starved.

    Returns:
        ``(heading, boarded, target)``: the committed direction, the requests that board
        this tick, and the floor to move toward (``None`` = stay put).
    """
    pending, here = stops(car), waiting_here(car)
    heading = next_heading(car, pending, here, now)
    boarded = boarding(car, heading, here, now)
    return heading, boarded, next_target(car, heading, boarded, pending)


def simulate(
    car: Car, now: int, stop_time: int, max_ticks: int = 100_000,
    *, boarded_at: dict[str, int] | None = None,
) -> dict[str, int]:
    """Replay the controller from tick ``now`` until the car is empty with no pickups.

    This is a fast-forward "what if": it runs the same rules the real car will follow, on
    a private copy, to predict when each passenger would be dropped off.

    Returns each rider's and pickup's drop-off tick keyed by request id. Mirrors the
    engine's tick: alight, plan, board, move; a stop holds the car ``stop_time`` ticks.
    ``boarded_at``, when given, is filled with each pickup's boarding tick.

    A moving car coasts straight to its target rather than re-planning on each floor in
    between. ``next_target`` returns the *nearest* stop ahead and every rider destination
    and pickup source is a stop, so no floor strictly between the car and its target can
    alight or board anyone -- the skipped ticks are ones on which nothing could happen.

    Args:
        car: Starting state. Copied, never mutated.
        now: The tick the replay starts at.
        stop_time: Ticks the car is held after anyone boards or alights.
        max_ticks: Safety cap on replayed ticks after ``now``; the default of 100,000 is
            far beyond any realistic run and exists only to turn a controller bug into an
            error instead of an endless loop.
        boarded_at: Optional output dict; when given, each pickup's boarding tick is written
            into it keyed by request id. ``None`` = don't record boarding ticks.

    Returns:
        Drop-off tick for every rider and pickup, keyed by request id.

    Raises:
        RuntimeError: the replay ran more than ``max_ticks`` ticks without finishing.
    """
    car = car.copy()
    t = now
    # Absolute tick at which the car may next move.
    held_until = now + car.held
    etas: dict[str, int] = {}
    while car.riders or car.pickups:
        # Guard against a rule bug that would otherwise loop forever.
        if t - now > max_ticks:
            raise RuntimeError("controller replay did not terminate")
        # Alight: drop off anyone whose destination is this floor, and hold the car.
        arrived = [r for r in car.riders if r.dest == car.floor]
        if arrived:
            car.riders = [r for r in car.riders if r.dest != car.floor]
            for r in arrived:
                etas[r.id] = t
            held_until = max(held_until, t + stop_time)
        # Plan: same rules the real dispatcher uses on the live car.
        heading, boarded, target = plan_car(car, t)
        car.heading = heading
        # Board: move the boarders from pickups to riders, and hold the car.
        if boarded:
            ids = {b.id for b in boarded}
            car.pickups = [p for p in car.pickups if p.id not in ids]
            car.riders.extend(boarded)
            if boarded_at is not None:
                for b in boarded:
                    boarded_at[b.id] = t
            held_until = max(held_until, t + stop_time)
        # Move: jump straight to the target (one tick per floor); otherwise wait a tick.
        if t >= held_until and target is not None and target != car.floor:
            t += abs(target - car.floor)
            car.floor = target
        else:
            t += 1
    return etas
