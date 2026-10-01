"""Shared test fixtures and test-only schedulers.

The schedulers are deliberately naive; they exist to exercise the engine.
"""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from elevator_sim.models import Direction, SimulationState
from elevator_sim.scheduler import CarAction, Scheduler

SAMPLE_REQUESTS = Path(__file__).resolve().parent.parent / "data" / "sample_requests.csv"
"""The worked example shipped with the project, used by the CLI and dispatch tests."""


class NaiveScheduler:
    """Everything rides car 0.

    Boards whoever waits at car 0's floor and fits, in request order. Then heads for the
    nearest floor with work (riders' destinations, waiting passengers' sources), keeping its
    direction while work lies ahead. Skips the current floor: anyone still waiting here did
    not fit, so keep moving rather than oscillate. Tracks its own heading, because the engine
    does not expose one -- a scheduler is responsible for its own commitments.
    """

    def __init__(self) -> None:
        self.heading = Direction.IDLE

    def step(self, state: SimulationState) -> Mapping[int, CarAction]:
        car = state.elevators[0]
        here = [r for r in state.waiting if r.source == car.floor]
        boarding = here[: car.free_capacity]
        board_ids = {r.id for r in boarding}

        stops = {r.request.dest for r in car.riders}
        stops |= {r.dest for r in boarding}
        stops |= {r.source for r in state.waiting if r.id not in board_ids}
        stops.discard(car.floor)
        if not stops:
            self.heading = Direction.IDLE
            return {0: CarAction(board=tuple(r.id for r in boarding))}

        if self.heading is Direction.UP:
            ahead = [f for f in stops if f > car.floor]
        elif self.heading is Direction.DOWN:
            ahead = [f for f in stops if f < car.floor]
        else:
            ahead = []
        target = min(ahead or stops, key=lambda f: abs(f - car.floor))
        self.heading = Direction.toward(car.floor, target)
        return {0: CarAction(target=target, board=tuple(r.id for r in boarding))}


class ScriptedScheduler:
    """Delegates every tick to a plain function, for tests that need exact control."""

    def __init__(self, fn: Callable[[SimulationState], Mapping[int, CarAction]]):
        self.fn = fn

    def step(self, state: SimulationState) -> Mapping[int, CarAction]:
        return self.fn(state)


class IdleScheduler:
    """Plans nothing, ever. Stalls the engine, which is the point."""

    def step(self, state: SimulationState) -> Mapping[int, CarAction]:
        return {}


@pytest.fixture
def naive_scheduler() -> Scheduler:
    return NaiveScheduler()
