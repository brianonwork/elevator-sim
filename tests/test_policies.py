"""Assignment policies on constructed snapshots. Cost numbers are hand-traced in comments."""

import pytest

from elevator_sim.algorithms.controller import Car
from elevator_sim.algorithms.policies import (
    EtaCost,
    Express,
    RoundRobin,
    ZoneBased,
    express_floors,
    nearest_car,
    nearest_car_eta,
    top_band,
)
from elevator_sim.models import (
    BuildingConfig,
    Direction,
    ElevatorView,
    Request,
    Rider,
    SimulationState,
)

UP, DOWN, IDLE = Direction.UP, Direction.DOWN, Direction.IDLE


def req(id, source, dest, time=0):
    return Request(time, id, source, dest)


def view(id, floor, riders=(), capacity=8, held_until=0):
    return ElevatorView(id=id, floor=floor, capacity=capacity,
                        riders=tuple(Rider(r, 0) for r in riders), held_until=held_until)


def state(cars, time=0, floors=30, stop_time=0, new=()):
    config = BuildingConfig(num_elevators=len(cars), num_floors=floors, capacity=8,
                            stop_time=stop_time)
    return SimulationState(time=time, config=config, elevators=tuple(cars),
                           waiting=tuple(new), new=tuple(new))


def empty(n):
    return {i: [] for i in range(n)}


def idle(n):
    return {i: IDLE for i in range(n)}


class TestRoundRobin:
    def test_cycles_through_cars_by_id(self):
        s = state([view(0, 1), view(1, 1), view(2, 1)])
        rr, r = RoundRobin(), req("n", 2, 4)
        assert [rr(s, empty(3), idle(3), r) for _ in range(4)] == [0, 1, 2, 0]

    def test_honours_a_narrowed_candidate_list(self):
        s = state([view(0, 1), view(1, 1), view(2, 1)])
        rr, r = RoundRobin(), req("n", 2, 4)
        only = [s.elevators[0], s.elevators[2]]
        assert [rr(s, empty(3), idle(3), r, only) for _ in range(3)] == [0, 2, 0]


class TestNearestCar:
    def test_smallest_distance_wins(self):
        s = state([view(0, 1), view(1, 8)])
        assert nearest_car(s, empty(2), idle(2), req("n", 6, 9)) == 1

    def test_tie_prefers_a_car_heading_toward_the_source(self):
        # Both three floors from 6. Car 0 at 3 heads down (away); car 1 at 9 heads down (toward).
        s = state([view(0, 3), view(1, 9)])
        assert nearest_car(s, empty(2), {0: DOWN, 1: DOWN}, req("n", 6, 9)) == 1

    def test_tie_then_lower_load_then_lower_id(self):
        s = state([view(0, 3, riders=[req("r", 1, 9)]), view(1, 3)])
        assert nearest_car(s, empty(2), idle(2), req("n", 6, 9)) == 1
        s = state([view(0, 3), view(1, 3)])
        assert nearest_car(s, empty(2), idle(2), req("n", 6, 9)) == 0

    def test_tie_then_fewer_committed_passengers_then_lower_id(self):
        # Both idle at floor 1, no riders aboard: car 0 has one assigned pickup (1 committed),
        # car 1 has none (0 committed), so car 1 wins despite the tied id order.
        s = state([view(0, 1), view(1, 1)])
        assigned = {0: [req("p", 1, 5)], 1: []}
        assert nearest_car(s, assigned, idle(2), req("n", 1, 9)) == 1
        assert nearest_car(s, empty(2), idle(2), req("n", 1, 9)) == 0


class TestNearestCarEta:
    def test_a_car_sweeping_away_is_not_near(self):
        # Car 0 is two floors above 18 but committed up to 50: up (t30), back down to 18 (t62),
        # turns up and boards. Car 1 idles five floors away and boards at t5.
        s = state([view(0, 20, riders=[req("r", 1, 50)]), view(1, 23)], floors=60)
        n, headings = req("n", 18, 30), {0: UP, 1: IDLE}
        assert nearest_car(s, empty(2), headings, n) == 0
        assert nearest_car_eta(s, empty(2), headings, n) == 1

    def test_a_full_car_is_not_near(self):
        # Car 0 at 10 is full (capacity 8) and bound for 30: passes 11 at t1 unable to board,
        # empties at 30 (t20), comes back to 11 (t39). Car 1 idles at 16 and boards at t5.
        riders = [req(f"r{i}", 1, 30) for i in range(8)]
        s = state([view(0, 10, riders=riders), view(1, 16)])
        n, headings = req("n", 11, 20), {0: UP, 1: IDLE}
        assert nearest_car(s, empty(2), headings, n) == 0
        assert nearest_car_eta(s, empty(2), headings, n) == 1

    def test_tie_then_fewer_committed_then_lower_id(self):
        s = state([view(0, 1), view(1, 1)])
        assert nearest_car_eta(s, {0: [req("p", 1, 5)], 1: []}, idle(2), req("n", 1, 9)) == 1
        assert nearest_car_eta(s, empty(2), idle(2), req("n", 1, 9)) == 0

    def test_a_narrowed_candidate_list_is_honoured(self):
        s = state([view(0, 1), view(1, 5), view(2, 9)])
        request = req("n", 8, 2)
        assert nearest_car_eta(s, empty(3), idle(3), request) == 2
        assert nearest_car_eta(s, empty(3), idle(3), request, s.elevators[:2]) == 1


class TestEtaCost:
    def test_delivery_beats_arrival(self):
        # Car 0 is one floor from the source but committed upward with a rider bound for 10:
        # it drops r at t4, reverses, collects n at 5 (t9), delivers at 2 (t12). Car 1 idles
        # four floors away: collects at t4, delivers at t7. Nearest-car picks car 0.
        s = state([view(0, 6, riders=[req("r", 1, 10)]), view(1, 9)])
        n, headings = req("n", 5, 2), {0: UP, 1: IDLE}
        assert EtaCost(power=1)(s, empty(2), headings, n) == 1
        assert nearest_car(s, empty(2), headings, n) == 0

    def test_cost_is_the_marginal_increase_in_summed_total_time(self):
        r, n = req("r", 1, 10), req("n", 5, 2)
        car0 = Car(floor=6, heading=UP, capacity=8, riders=[r])
        car1 = Car(floor=9, heading=IDLE, capacity=8)
        assert EtaCost(power=1).cost(car0, n, now=0, stop_time=0) == 12   # r unchanged, n total 12
        assert EtaCost(power=1).cost(car1, n, now=0, stop_time=0) == 7

    def test_assigned_pickups_count_in_the_cost(self):
        # Car 0 idles at 4 with p (1->2) assigned. Taking n (5->9) too: up to 5 (t1), n off at 9
        # (t5), down to 1 (t13), p off at 2 (t14) instead of t4. Delta 5 + 10 = 15. Car 1 at 8:
        # collects n at t3, delivers t7. Delta 7.
        p, n = req("p", 1, 2), req("n", 5, 9)
        s = state([view(0, 4), view(1, 8)])
        assert EtaCost(power=1)(s, {0: [p], 1: []}, idle(2), n) == 1
        assert EtaCost(power=1).cost(Car(floor=4, heading=IDLE, capacity=8, pickups=[p]),
                                     n, now=0, stop_time=0) == 15

    def test_power_two_protects_the_long_waited_rider(self):
        # Tick 20, stops hold one tick. r (requested t0) is aboard car 0 at 25, bound for 30.
        # n wants 26->28, on car 0's way. Car 0 with n: n off t24 (total 4), r off t27 instead
        # of t25. p=1 cost 4+2=6; p=2 cost 16+(27^2-25^2)=120. Car 1 idle at 20: n off t29
        # (total 9). p=1 cost 9; p=2 cost 81.
        r, n = req("r", 1, 30, time=0), req("n", 26, 28, time=20)
        s = state([view(0, 25, riders=[r]), view(1, 20)], time=20, stop_time=1)
        headings = {0: UP, 1: IDLE}
        assert EtaCost(power=1)(s, empty(2), headings, n) == 0
        assert EtaCost(power=2)(s, empty(2), headings, n) == 1

    def test_a_non_integer_power_costs_exactly_the_new_riders_own_term(self):
        # Tick 5: car at 1 heading up collects a (1->2) and b (1->3), dropping them at t6 and
        # t7. n (10->12) is appended past the end of the route, so neither ETA moves and the
        # cost is n's own term alone: off at t16, total 11. Subtracting two float totals left
        # a rounding residue here, which could decide a tie the tie-break should.
        a, b = req("a", 1, 2, time=0), req("b", 1, 3, time=1)
        car = Car(floor=1, heading=UP, capacity=8, pickups=[a, b])
        n = req("n", 10, 12, time=5)
        assert EtaCost(power=1.5).cost(car, n, now=5, stop_time=0) == 11 ** 1.5

    def test_tie_breaks_on_load_then_id(self):
        s = state([view(0, 1, riders=[req("r", 1, 5)]), view(1, 1)])
        assert EtaCost()(s, empty(2), {0: UP, 1: IDLE}, req("n", 1, 5)) == 1


class TestCandidates:
    def test_a_narrowed_candidate_list_is_honoured(self):
        s = state([view(0, 1), view(1, 5), view(2, 9)])
        request = req("n", 8, 2)
        assert nearest_car(s, empty(3), idle(3), request) == 2       # car 2 is nearest
        narrowed = [s.elevators[0], s.elevators[1]]
        assert nearest_car(s, empty(3), idle(3), request, narrowed) == 1

    def test_the_snapshot_still_describes_the_whole_fleet(self):
        """The old narrowing replaced state.elevators, so the snapshot lied: `elevators` no
        longer described the fleet while `waiting` and `new` still did."""
        seen = []

        def spy(state, assigned, headings, request, candidates=None):
            seen.append((len(state.elevators), len(candidates or state.elevators)))
            return (candidates or state.elevators)[0].id

        s = state([view(0, 44), view(1, 1), view(2, 1), view(3, 1)], floors=60)
        ZoneBased(inner=spy)(s, empty(4), idle(4), req("n", 45, 50))
        assert seen == [(4, 1)]  # four cars in the fleet, one in the zone


class TestExpress:
    def test_express_band_is_the_lobby_plus_the_top_third(self):
        assert top_band(60) == (41, 60)
        assert express_floors(60) == frozenset({1, *range(41, 61)})
        assert express_floors(30) == frozenset({1, *range(21, 31)})

    def recording(self):
        """An inner policy that records the candidate ids it was offered and picks the last."""
        offered = []

        def inner(state, assigned, headings, request, candidates=None):
            offered.append([c.id for c in candidates])
            return candidates[-1].id

        return inner, offered

    def policy(self, inner, express_cars=frozenset({2})):
        return Express(inner, express_cars=express_cars, floors=express_floors(30))

    def test_a_local_trip_is_offered_only_the_local_cars(self):
        inner, offered = self.recording()
        s = state([view(0, 1), view(1, 1), view(2, 1)])
        assert self.policy(inner)(s, empty(3), idle(3), req("n", 5, 12)) == 1
        assert offered == [[0, 1]]

    @pytest.mark.parametrize("source,dest", [(1, 25), (25, 1), (22, 30)])
    def test_an_express_trip_is_offered_only_the_express_cars(self, source, dest):
        inner, offered = self.recording()
        s = state([view(0, 1), view(1, 1), view(2, 1)])
        assert self.policy(inner)(s, empty(3), idle(3), req("n", source, dest)) == 2
        assert offered == [[2]]

    def test_two_express_cars_share_the_express_trips(self):
        inner, offered = self.recording()
        s = state([view(0, 1), view(1, 1), view(2, 1)])
        policy = self.policy(inner, express_cars=frozenset({1, 2}))
        assert policy(s, empty(3), idle(3), req("n", 1, 25)) == 2
        policy(s, empty(3), idle(3), req("m", 5, 12))
        assert offered == [[1, 2], [0]]

    def test_a_trip_with_one_end_in_the_band_is_local(self):
        # Lobby to floor 12: the express car cannot stop at 12, so it is not offered.
        inner, offered = self.recording()
        s = state([view(0, 1), view(1, 1), view(2, 1)])
        self.policy(inner)(s, empty(3), idle(3), req("n", 1, 12))
        assert offered == [[0, 1]]

    def test_candidates_are_narrowed_further_not_replaced(self):
        inner, offered = self.recording()
        s = state([view(0, 1), view(1, 1), view(2, 1)])
        only = [s.elevators[1], s.elevators[2]]
        self.policy(inner)(s, empty(3), idle(3), req("n", 5, 12), only)
        assert offered == [[1]]

    def test_a_far_away_express_car_still_owns_the_express_trip(self):
        # Car 2 (express) is at the top; car 0 sits at the lobby and would be far cheaper.
        # The lobby-to-top rider still goes to car 2: express trips are owned, not shared.
        s = state([view(0, 1), view(1, 1), view(2, 30)])
        policy = Express(EtaCost(), express_cars=frozenset({2}), floors=express_floors(30))
        assert policy(s, empty(3), idle(3), req("n", 1, 25)) == 2
