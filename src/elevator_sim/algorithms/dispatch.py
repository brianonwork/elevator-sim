"""Destination dispatch: bind each request to one car at release and drive cars by LOOK.

**Destination dispatch** means a passenger's request (source and destination floor) is given to
exactly one car the moment it appears, and that choice is never revisited. **LOOK** is the sweep
rule the cars then follow: keep going one way while there are stops ahead, then turn round (see
``controller.py`` for the full rules).

The policy chooses the car. The controller (``controller.py``) decides what each car does,
including keeping a seat for a rider who has waited ``starve_limit`` ticks, which is what
ends every wait even though no request is ever reassigned.
This class owns the only per-car state: each car's assigned pickups and committed heading.
(``RoundRobin`` also remembers its last pick, but that is policy state, not per-car.)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..models import Direction, Request, SimulationState
from ..scheduler import CarAction
from .controller import from_view, plan_car, starve_limit
from .policies import Policy


class DestinationDispatch:
    """Scheduler implementing destination dispatch over a pluggable policy."""

    def __init__(self, policy: Policy, park: Callable[[int], int] | None = None) -> None:
        """Initialise with the policy that assigns each request to a car.

        ``park`` maps a car id to the floor it drifts back to when idle. Left unset, cars
        stay where they last stopped, which is what every scheduler but ``zone-based`` does.

        Args:
            policy: Chooses which car takes each newly released request.
            park: Car id -> its park floor (1-based), the floor an idle car returns to;
                ``None`` = idle cars stay put.
        """
        self.policy = policy
        self.park = park
        self.assigned: dict[int, list[Request]] = {}
        """Car id -> assigned, still-waiting pickups in assignment order."""
        self.heading: dict[int, Direction] = {}
        """Car id -> the heading committed to last tick."""

    def step(self, state: SimulationState) -> Mapping[int, CarAction]:
        """Plan the next move for each car.

        Args:
            state: This tick's snapshot from the engine.

        Returns:
            A mapping of car id to CarAction for this tick: where each car heads next
            (``None`` target = stay put) and which of its pickups board now.
        """
        # Forget pickups that are no longer waiting (they have already boarded),
        # and give any car seen for the first time an idle heading.
        waiting_ids = {r.id for r in state.waiting}
        for view in state.elevators:
            self.heading.setdefault(view.id, Direction.IDLE)
            self.assigned[view.id] = [
                r for r in self.assigned.get(view.id, []) if r.id in waiting_ids
            ]

        # Assign each newly released request to one car, permanently.
        for request in state.new:
            car_id = self.policy(state, self.assigned, self.heading, request)
            self.assigned[car_id].append(request)

        # Run the controller for each car and turn its decision into an action.
        plan: dict[int, CarAction] = {}
        # Same threshold the policies' replays use, so their ETAs predict this car.
        starve_after = starve_limit(state.config)
        for view in state.elevators:
            car = from_view(
                view,
                self.heading[view.id],
                self.assigned[view.id],
                state.time,
                park=None if self.park is None else self.park(view.id),
                starve_after=starve_after,
            )
            heading, boarded, target = plan_car(car, state.time)
            # Remember the heading so next tick's decision can honour the commitment.
            self.heading[view.id] = heading
            boarded_ids = tuple(b.id for b in boarded)
            # Boarders are no longer pickups for this car.
            if boarded:
                taken = set(boarded_ids)
                self.assigned[view.id] = [r for r in self.assigned[view.id] if r.id not in taken]
            plan[view.id] = CarAction(target=target, board=boarded_ids)
        return plan
