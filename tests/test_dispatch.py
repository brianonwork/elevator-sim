"""End-to-end through the engine. Every timeline is hand-traced against the tick order."""

import random

import pytest
from conftest import SAMPLE_REQUESTS as SAMPLE

from elevator_sim.algorithms import (
    BUILTINS,
    EtaCost,
    create,
    express_car_count,
    express_floors,
    nearest_car,
)
from elevator_sim.algorithms.dispatch import DestinationDispatch
from elevator_sim.csv_io import read_requests
from elevator_sim.models import BuildingConfig, Request
from elevator_sim.scheduler import CarAction
from elevator_sim.simulation import Simulation

POLICIES = sorted(BUILTINS)
"""Every built-in, so a scheduler added to the registry is covered here too."""


def run(requests, policy="eta-cost", elevators=1, floors=10, capacity=8, stop_time=0):
    config = BuildingConfig(
        num_elevators=elevators, num_floors=floors, capacity=capacity, stop_time=stop_time
    )
    result = Simulation(config, create(policy, config), requests).run(max_ticks=10_000)
    return {p.request.id: p for p in result.passengers}, result


def timeline(p):
    return (p.elevator_id, p.pickup_time, p.dropoff_time)


def test_wrong_direction_passenger_waits_for_the_return_pass():
    by_id, _ = run([Request(0, "a", 1, 6), Request(0, "b", 3, 1)])
    assert timeline(by_id["a"]) == (0, 0, 5)
    assert timeline(by_id["b"]) == (0, 8, 10)


def test_heading_survives_holds_and_end_of_sweep_pickup_boards():
    # stop_time 2. a boards t0 and the car is held for t0 and t1, so it first moves at t2 and
    # is on 5 for t6: a off (held t6, t7). Car keeps UP to 7 (there for t10): nothing above,
    # c waits behind at 4, but d wants up from here, so d boards (rule 3; held t10, t11),
    # off at 9 for t14 (held t14, t15). Reverse; c boards at 4 for t21 (held t21, t22);
    # off at 1 for t26. Positions: 1,1,1,2,3,4,5,5,5,6,7,7,7,8,9,9,9,8,7,6,5,4,4,4,3,2,1.
    by_id, _ = run(
        [Request(0, "a", 1, 5), Request(0, "d", 7, 9), Request(0, "c", 4, 1)], stop_time=2
    )
    assert timeline(by_id["a"]) == (0, 0, 6)
    assert timeline(by_id["d"]) == (0, 10, 14)
    assert timeline(by_id["c"]) == (0, 21, 26)


def test_capacity_overflow_is_served_on_the_next_visit():
    by_id, _ = run([Request(0, "a", 1, 3), Request(0, "b", 1, 3)], capacity=1)
    assert timeline(by_id["a"]) == (0, 0, 2)
    assert timeline(by_id["b"]) == (0, 4, 6)


def test_burst_spreads_across_cars_when_sharing_costs_more():
    # stop_time 1. a goes to car 0. b on car 0 would wait through a's stop at 5 and finish at
    # t10; alone on car 1 it finishes at t9. Cost 10 vs 9, so b goes to car 1.
    by_id, _ = run([Request(0, "a", 1, 5), Request(0, "b", 1, 9)], elevators=2, stop_time=1)
    assert timeline(by_id["a"]) == (0, 0, 5)
    assert timeline(by_id["b"]) == (1, 0, 9)


class TestSampleFile:
    # passenger1 1->51 t0, passenger2 1->37 t0, passenger3 20->1 t10; two cars, 60 floors.

    def test_eta_cost(self):
        # p1 and p2 tie between cars and share car 0. At t10 car 0 is at 11 heading up: it would
        # deliver p3 at t100; idle car 1 reaches 20 at t29 and delivers at t48.
        by_id, result = run(read_requests(SAMPLE), elevators=2, floors=60)
        assert timeline(by_id["passenger1"]) == (0, 0, 50)
        assert timeline(by_id["passenger2"]) == (0, 0, 36)
        assert timeline(by_id["passenger3"]) == (1, 29, 48)
        assert result.positions_log[0] == (0, (1, 1))

    def test_nearest_car_sends_passenger3_to_the_busy_car(self):
        # Car 0 at 11 is 9 floors from 20 and heading toward it; car 1 is 19 away.
        by_id, _ = run(read_requests(SAMPLE), policy="nearest-car", elevators=2, floors=60)
        assert timeline(by_id["passenger3"]) == (0, 81, 100)

    def test_round_robin_alternates(self):
        by_id, _ = run(read_requests(SAMPLE), policy="round-robin", elevators=2, floors=60)
        assert timeline(by_id["passenger1"]) == (0, 0, 50)
        assert timeline(by_id["passenger2"]) == (1, 0, 36)
        assert timeline(by_id["passenger3"]) == (0, 81, 100)


def test_express_car_owns_the_long_trip_and_the_local_car_the_short_one():
    # Under `express` on 2 cars x 60 floors, car 1 is express (lobby + 41..60). 'top' is a
    # band trip so it is car 1's regardless of cost, and 'local' is car 0's; each car has
    # one rider, so neither delays the other (local boards at t1: car 0 climbs from 1 to 2).
    by_id, _ = run(
        [Request(0, "local", 2, 5), Request(0, "top", 1, 55)],
        policy="express",
        elevators=2,
        floors=60,
        stop_time=1,
    )
    assert timeline(by_id["local"]) == (0, 1, 5)
    assert timeline(by_id["top"]) == (1, 0, 55)


def test_express_car_count_is_one_per_four_cars_at_least_one():
    assert [express_car_count(n) for n in (2, 3, 4, 5, 7, 8, 12)] == [1, 1, 1, 1, 1, 2, 3]


def test_express_rejects_a_building_whose_band_is_every_floor():
    # Two floors: the band (lobby plus top third) is both floors, so no trip is local.
    with pytest.raises(ValueError, match="band is every floor"):
        create("express", BuildingConfig(num_elevators=4, num_floors=2, capacity=8))
    # Three floors leave floor 2 to the local cars.
    create("express", BuildingConfig(num_elevators=4, num_floors=3, capacity=8))


@pytest.mark.parametrize("elevators,floors", [(3, 30), (8, 60)])
def test_express_cars_and_local_cars_own_disjoint_trips(elevators, floors):
    # Random traffic: every band trip rode an express car (the last one or two), every
    # other trip a local car, and nobody was stranded by the split.
    by_id, _ = run(
        random_requests(3, n=120, floors=floors),
        policy="express",
        elevators=elevators,
        floors=floors,
        capacity=4,
    )
    band = express_floors(floors)
    express_cars = set(range(elevators - express_car_count(elevators), elevators))
    assert express_cars == ({2} if elevators == 3 else {6, 7})
    for p in by_id.values():
        is_band = p.request.source in band and p.request.dest in band
        assert (p.elevator_id in express_cars) == is_band, p.request
    assert any(p.elevator_id in express_cars for p in by_id.values())
    assert all(p.is_done for p in by_id.values())


def test_plan_covers_every_car():
    config = BuildingConfig(num_elevators=3, num_floors=10, capacity=8)
    sim = Simulation(config, DestinationDispatch(EtaCost()), [Request(0, "a", 1, 5)])
    plan = sim.scheduler.step(sim.state)
    assert set(plan) == {0, 1, 2}
    assert all(isinstance(a, CarAction) for a in plan.values())


def random_requests(seed, n=60, floors=20, horizon=40):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        src, dst = rng.sample(range(1, floors + 1), 2)
        out.append(Request(rng.randrange(horizon), f"p{i}", src, dst))
    return out


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("seed", [1, 7, 42])
def test_random_burst_is_served_without_reversing_a_loaded_car(policy, seed):
    by_id, _ = run(random_requests(seed), policy=policy, elevators=3, floors=20, capacity=2)
    assert all(p.is_done and p.wait_time >= 0 for p in by_id.values())
    # With free stops a committed car moves one floor per tick toward each rider's floor, so
    # travel time equals floor distance. Any reversal with riders aboard would lengthen it.
    assert all(p.travel_time == abs(p.request.dest - p.request.source) for p in by_id.values())


@pytest.mark.parametrize("policy", POLICIES)
def test_random_burst_with_stop_time_is_served_without_reversing_a_loaded_car(policy):
    by_id, result = run(
        random_requests(3), policy=policy, elevators=3, floors=20, capacity=2, stop_time=1
    )
    assert all(p.is_done for p in by_id.values())
    # With stop_time > 0 a hold can pause a car between ticks, so travel_time no longer equals
    # floor distance (unlike the free-stops case above); check monotonicity directly instead.
    positions_by_time = dict(result.positions_log)
    for p in by_id.values():
        floors = [
            positions_by_time[t][p.elevator_id] for t in range(p.pickup_time, p.dropoff_time + 1)
        ]
        if p.request.dest > p.request.source:
            assert floors == sorted(floors)
        else:
            assert floors == sorted(floors, reverse=True)


def test_a_request_is_bound_once_at_release_and_that_car_collects_it():
    """Destination dispatch, as the prompt states it, in one test.

    Three clauses at once: assigned immediately (at release), assigned to exactly one car,
    and never reassigned -- the car that picks a passenger up is the car chosen on their
    release tick. Re-running the policy over the waiting pool every tick currently fails 20
    tests, but as SchedulingError crashes and shifted timelines, never as a stated violation.
    """
    config = BuildingConfig(num_elevators=3, num_floors=20, capacity=4)
    requests = [
        Request(t, f"p{t}", source, dest)
        for t in range(0, 40, 3)
        for source, dest in [(1 + (t * 7) % 20, 1 + (t * 13) % 20)]
        if source != dest
    ]
    bound: dict[str, tuple[int, int]] = {}

    def spy(state, assigned, headings, request):
        car_id = nearest_car(state, assigned, headings, request)
        assert request.id not in bound, f"{request.id} was assigned twice"
        assert request.time == state.time, f"{request.id} bound at {state.time}, not its time"
        bound[request.id] = (car_id, state.time)
        return car_id

    result = Simulation(config, DestinationDispatch(spy), requests).run(max_ticks=10_000)
    assert len(bound) == len(requests)
    for p in result.passengers:
        assert p.elevator_id == bound[p.request.id][0], (
            f"{p.request.id} was bound to car {bound[p.request.id][0]} "
            f"but boarded car {p.elevator_id}"
        )
