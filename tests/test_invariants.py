"""The spec's objectives, as invariants every scheduler must satisfy.

These run every built-in over random traffic. They are deliberately about behaviour the
engine guarantees or the spec requires, not about any scheduler's strategy, so a better
scheduler never breaks them and a broken one always does.
"""

from __future__ import annotations

import random

import pytest

from elevator_sim.algorithms import BUILTINS
from elevator_sim.models import BuildingConfig, Request
from elevator_sim.simulation import Simulation

SCHEDULERS = sorted(BUILTINS)
SEEDS = range(5)


def random_requests(seed: int, n: int = 60, floors: int = 20, horizon: int = 80) -> list[Request]:
    rng = random.Random(seed)
    requests = []
    for i in range(n):
        source = rng.randint(1, floors)
        dest = rng.choice([f for f in range(1, floors + 1) if f != source])
        requests.append(Request(rng.randint(0, horizon), f"p{i}", source, dest))
    requests.sort(key=lambda r: (r.time, r.id))
    return requests


BUILDINGS = {
    "free-stops": BuildingConfig(num_elevators=3, num_floors=20, capacity=4),
    "costly-stops": BuildingConfig(num_elevators=3, num_floors=20, capacity=4, stop_time=2),
    "one-seat-one-car": BuildingConfig(num_elevators=1, num_floors=20, capacity=1),
    "one-seat-two-cars": BuildingConfig(num_elevators=2, num_floors=20, capacity=1),
}
"""Free stops and stops that cost time. The invariants are properties of the spec, so they
must hold in both -- and stop_time otherwise rests on a handful of hand-traced tests. Every
scheduler, the express one included, runs on both.

The two one-seat buildings are there for the starvation rule: with one seat a queue builds
past ``starve_limit``, so the seat a car keeps for its oldest rider is its only seat, which
is the case most likely to leave a car with nothing it is allowed to do."""


@pytest.fixture(params=sorted(BUILDINGS), ids=sorted(BUILDINGS))
def config(request) -> BuildingConfig:
    building = BUILDINGS[request.param]
    if request.node.callspec.params.get("name") == "express" and building.num_elevators < 2:
        pytest.skip("express needs two cars")
    return building


@pytest.mark.parametrize("name", SCHEDULERS)
@pytest.mark.parametrize("seed", SEEDS)
def test_capacity_and_position_hold_every_tick(name, seed, config):
    sim = Simulation(config, BUILTINS[name](config), random_requests(seed))
    while not sim.is_done:
        sim.step()
        for e in sim.elevators:
            assert len(e.riders) <= e.capacity
            assert 1 <= e.floor <= config.num_floors


@pytest.mark.parametrize("name", SCHEDULERS)
@pytest.mark.parametrize("seed", SEEDS)
def test_every_released_passenger_is_in_exactly_one_place(name, seed, config):
    sim = Simulation(config, BUILTINS[name](config), random_requests(seed))
    while not sim.is_done:
        sim.step()
        aboard = sum(len(e.riders) for e in sim.elevators)
        done = sum(1 for p in sim.passengers if p.is_done)
        assert sim.waiting + aboard + done == len(sim.passengers)


@pytest.mark.parametrize("name", SCHEDULERS)
@pytest.mark.parametrize("seed", SEEDS)
def test_everyone_is_eventually_served_with_consistent_timestamps(name, seed, config):
    requests = random_requests(seed)
    result = Simulation(config, BUILTINS[name](config), requests).run(max_ticks=50_000)
    assert len(result.passengers) == len(requests)
    floors_at = dict(result.positions_log)
    for p in result.passengers:
        assert p.is_done, f"{p.request.id} never served"
        assert p.request.time <= p.pickup_time <= p.dropoff_time
        assert floors_at[p.pickup_time][p.elevator_id] == p.request.source
        assert floors_at[p.dropoff_time][p.elevator_id] == p.request.dest


@pytest.mark.parametrize("name", SCHEDULERS)
def test_a_car_moves_at_most_one_floor_per_tick(name, config):
    result = Simulation(config, BUILTINS[name](config), random_requests(0)).run(max_ticks=50_000)
    for (_, before), (_, after) in zip(
        result.positions_log, result.positions_log[1:], strict=False
    ):
        for a, b in zip(before, after, strict=True):
            assert abs(b - a) <= 1


@pytest.mark.parametrize("name", SCHEDULERS)
@pytest.mark.parametrize("seed", SEEDS)
def test_a_scheduler_never_sees_a_request_before_its_time(name, seed, config):
    """The prompt's "do not peek ahead", asserted from the scheduler's side.

    The engine test covers one car and one request; this is the constraint as every built-in
    actually experiences it, in the file a reviewer reads to find it.
    """
    inner = BUILTINS[name](config)
    seen = []

    class Watching:
        def step(self, state):
            seen.append(state.time)
            assert all(r.time <= state.time for r in state.waiting)
            assert all(r.time == state.time for r in state.new)
            return inner.step(state)

    Simulation(config, Watching(), random_requests(seed)).run(max_ticks=50_000)
    assert seen == list(range(len(seen))), "the scheduler must be asked once per tick, in order"


@pytest.mark.parametrize("name", SCHEDULERS)
@pytest.mark.parametrize("seed", SEEDS)
def test_a_draining_burst_leaves_no_rider_waiting_past_a_few_round_trips(name, seed, config):
    """Waits stay bounded when arrivals stop, so a burst drains.

    ``test_everyone_is_eventually_served`` passes at max_ticks=50_000, which a scheduler that
    left one rider for 5,000 ticks would also pass; this attaches a number. 60 requests over
    80 ticks in a 20-floor building is a burst: at 0.75 arrivals/tick it runs about 2.5x a
    rough three-car throughput of ~0.3/tick (capacity 4, ~40-tick round trip). But arrivals
    stop at t=80 and total service is only about five round trips (~200 ticks), so a wait past
    an 8-round-trip bound is a bug, not congestion.

    It does not show that waits stay bounded under arrivals that never stop. That is the
    controller's starved-rider seat, tested in ``test_starvation.py`` and, with one-seat
    cars, by every other invariant in this file.
    """
    round_trip = 2 * config.num_floors
    requests = random_requests(seed)
    # Four seats: the 8 round trips argued above. One seat: the burst queues far longer, so
    # the bound is the worst a working fleet can do -- every rider assigned to one car and
    # each costing it a whole round trip. The measured worst is about a third of that (790
    # of 2,400 ticks), and a rider left behind by the starvation rule would be far past it.
    round_trips = 8 if config.capacity > 1 else len(requests)
    result = Simulation(config, BUILTINS[name](config), requests).run(max_ticks=50_000)
    worst = max(result.passengers, key=lambda p: p.wait_time)
    assert worst.wait_time < round_trips * round_trip, (
        f"{worst.request.id} waited {worst.wait_time} ticks for a {round_trip}-tick round trip"
    )
