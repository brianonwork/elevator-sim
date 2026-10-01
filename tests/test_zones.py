"""Zone-based dispatch: the home-zone bias, its fallback, and idle parking."""

import pytest
from test_policies import empty, idle, req, state, view

from elevator_sim import BuildingConfig, Request, Simulation, create
from elevator_sim.algorithms import DestinationDispatch, ZoneBased, build_zone_based, zones
from elevator_sim.algorithms.controller import Car, next_target, plan_car
from elevator_sim.models import Direction

IDLE = Direction.IDLE


class TestZones:
    @pytest.mark.parametrize(
        "cars,floors",
        [(4, 60), (8, 60), (2, 60), (3, 31), (4, 2), (5, 5), (3, 2), (10, 5), (7, 3)],
    )
    def test_every_zone_is_non_empty_and_inside_the_building(self, cars, floors):
        ranges = zones(cars, floors)
        assert len(ranges) == cars
        for lo, hi in ranges:
            assert 1 <= lo <= hi <= floors, f"empty or out-of-range zone ({lo}, {hi})"

    @pytest.mark.parametrize(
        "cars,floors",
        [(4, 60), (8, 60), (2, 60), (3, 31), (4, 2), (5, 5), (3, 2), (10, 5), (7, 3)],
    )
    def test_zones_cover_the_building(self, cars, floors):
        covered = {f for lo, hi in zones(cars, floors) for f in range(lo, hi + 1)}
        assert covered == set(range(1, floors + 1))

    @pytest.mark.parametrize("cars,floors", [(4, 60), (2, 60), (3, 31), (5, 5)])
    def test_zones_are_disjoint_while_there_are_floors_to_spare(self, cars, floors):
        # Summing the widths counts any overlap twice, so equality is disjointness.
        assert sum(hi - lo + 1 for lo, hi in zones(cars, floors)) == floors

    def test_zones_are_near_equal(self):
        assert zones(4, 60) == ((1, 15), (16, 30), (31, 45), (46, 60))

    def test_more_cars_than_floors_share_single_floor_zones(self):
        assert zones(7, 3) == ((1, 1), (2, 2), (3, 3), (1, 1), (2, 2), (3, 3), (1, 1))


class TestAssignment:
    def test_the_zone_car_wins_even_when_another_is_nearer(self):
        # Car 0 sits on floor 44, one floor from the source; floor 45 is car 2's zone.
        cars = [view(0, 44), view(1, 1), view(2, 1), view(3, 1)]
        s = state(cars, floors=60)
        assert ZoneBased()(s, empty(4), idle(4), req("n", 45, 50)) == 2

    def test_an_over_committed_zone_car_is_passed_over(self):
        # Capacity 8 x load_factor 1.5 = 12; car 2 holds 12 assigned pickups already.
        cars = [view(0, 44), view(1, 1), view(2, 1), view(3, 1)]
        s = state(cars, floors=60)
        assigned = empty(4) | {2: [req(f"q{i}", 40, 50) for i in range(12)]}
        assert ZoneBased()(s, assigned, idle(4), req("n", 45, 50)) == 0

    def test_a_narrowed_candidate_list_without_the_zone_car_falls_back_to_the_rest(self):
        # Car 2 owns floor 45 but a caller (an outer policy) has excluded it.
        cars = [view(0, 44), view(1, 1), view(2, 1), view(3, 1)]
        s = state(cars, floors=60)
        only = [cars[0], cars[1], cars[3]]
        assert ZoneBased()(s, empty(4), idle(4), req("n", 45, 50), only) == 0


class TestParking:
    def test_an_idle_car_with_a_park_floor_drifts_to_it(self):
        car = Car(floor=60, heading=IDLE, capacity=8, park=23)
        assert plan_car(car, 0) == (IDLE, [], 23)

    def test_without_a_park_floor_an_idle_car_stays_put(self):
        assert next_target(Car(floor=60, heading=IDLE, capacity=8), IDLE, []) is None

    def test_parking_never_pre_empts_committed_work(self):
        car = Car(floor=10, heading=IDLE, capacity=8, pickups=[req("a", 30, 40)], park=23)
        _, _, target = plan_car(car, 0)
        assert target == 30  # the pickup, not the park floor

    def test_park_survives_a_copy(self):
        # Car.copy() is currently only ever reached with park=None (both call sites run
        # through EtaCost, which ZoneBased never invokes), so this guards a latent contract
        # rather than live behaviour -- but it becomes live the moment parking is combined
        # with an ETA-based policy.
        assert Car(floor=1, heading=IDLE, capacity=8, park=7).copy().park == 7


def test_cars_spread_to_their_zone_centres_once_the_building_goes_quiet():
    config = BuildingConfig(num_elevators=4, num_floors=60, capacity=8)
    sim = Simulation(config, build_zone_based(config), [Request(0, "a", 1, 50)])
    for _ in range(120):  # long enough to deliver the rider and drift home
        sim.step()
    assert tuple(e.floor for e in sim.elevators) == (8, 23, 38, 53)  # the zone centres


def test_on_one_car_only_parking_separates_zone_based_from_the_rest():
    """With a single car every scheduler must agree: there is no assignment to make.

    ``zone-based`` is the one exception, and only because it also parks. Disable parking
    and it matches again -- which is what pins the difference on parking rather than on a
    bug in the zone rule.
    """
    config = BuildingConfig(num_elevators=1, num_floors=30, capacity=8)
    requests = [Request(0, "a", 5, 20), Request(60, "b", 12, 3), Request(200, "c", 8, 25)]

    def log(scheduler):
        return Simulation(config, scheduler, requests).run().positions_log

    baseline = log(create("eta-cost", config))
    assert all(log(create(n, config)) == baseline
               for n in ("eta-cost-fair", "nearest-car", "nearest-car-eta", "round-robin"))
    assert log(DestinationDispatch(ZoneBased())) == baseline   # assignment alone agrees
    assert log(build_zone_based(config)) != baseline           # parking is the difference


@pytest.mark.parametrize("cars,floors", [(3, 2), (5, 3), (10, 5)])
def test_zone_based_serves_a_fleet_larger_than_the_building(cars, floors):
    """More cars than floors is a legal config; every car still needs a real park floor."""
    config = BuildingConfig(num_elevators=cars, num_floors=floors, capacity=4)
    result = Simulation(
        config, build_zone_based(config), [Request(0, "a", 1, floors)]
    ).run(max_ticks=500)
    assert result.passengers[0].is_done
