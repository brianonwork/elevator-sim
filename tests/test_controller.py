"""The LOOK controller and its replay. Every expected number is hand-traced in a comment."""

import random

import pytest

from elevator_sim.algorithms import EtaCost, controller
from elevator_sim.algorithms.controller import (
    Car,
    boarding,
    from_view,
    next_heading,
    next_target,
    overdue,
    plan_car,
    simulate,
    starve_limit,
    stops,
    waiting_here,
)
from elevator_sim.algorithms.dispatch import DestinationDispatch
from elevator_sim.models import BuildingConfig, Direction, ElevatorView, Request, Rider
from elevator_sim.scheduler import ReplayError
from elevator_sim.simulation import Simulation

UP, DOWN, IDLE = Direction.UP, Direction.DOWN, Direction.IDLE


def req(id, source, dest, time=0):
    return Request(time, id, source, dest)


def car(floor, heading=IDLE, riders=(), pickups=(), capacity=8, held=0, starve_after=None):
    return Car(
        floor=floor,
        heading=heading,
        capacity=capacity,
        riders=list(riders),
        pickups=list(pickups),
        held=held,
        starve_after=starve_after,
    )


class TestBasics:
    def test_waiting_here_is_pickups_on_this_floor_in_order(self):
        c = car(5, pickups=[req("p", 5, 2), req("q", 3, 1), req("r", 5, 9)])
        assert [p.id for p in waiting_here(c)] == ["p", "r"]

    def test_stops_excludes_current_floor(self):
        c = car(5, riders=[req("r", 1, 9)], pickups=[req("p", 5, 2), req("q", 3, 1)])
        assert stops(c) == {9, 3}

    def test_from_view_copies_riders_pickups_and_remaining_hold(self):
        r = req("r", 1, 9)
        view = ElevatorView(id=0, floor=4, capacity=3, riders=(Rider(r, 1),), held_until=7)
        c = from_view(view, UP, [req("p", 6, 8)], now=5)
        assert (c.floor, c.heading, c.capacity, c.riders, [p.id for p in c.pickups], c.held) == (
            4,
            UP,
            3,
            [r],
            ["p"],
            2,
        )
        assert c.free_capacity == 2


class TestNextHeading:
    def test_rule1_idle_heading_follows_the_rider_not_the_nearest_stop(self):
        # Discriminating on purpose: the nearest stop is the pickup at 4, so rule 5 would
        # reverse and carry the seated rider away from floor 9. Deleting rule 1 flips this
        # to DOWN, which is the whole point of the rule.
        assert (
            next_heading(car(5, IDLE, riders=[req("r", 1, 9)], pickups=[req("p", 4, 1)]), now=0)
            is UP
        )

    def test_a_loaded_car_with_nothing_ahead_reverses_instead_of_freezing(self):
        # heading UP with the only rider bound for 2: nothing is ahead, so the car must turn
        # round. Keeping the heading returns target None and the car stands still forever,
        # re-committing UP every tick -- boarding() makes this unreachable through the engine,
        # but the controller should not depend on that to terminate.
        c = car(5, UP, capacity=2, riders=[req("r", 5, 2)])
        assert next_heading(c, now=0) is DOWN
        assert plan_car(c, 0)[2] == 2
        assert simulate(c, now=0, stop_time=0) == {"r": 3}

    def test_rule2_stop_ahead_keeps_heading(self):
        assert next_heading(car(5, UP, pickups=[req("p", 8, 1)]), now=0) is UP

    def test_rule3_pickup_here_in_heading_extends_the_sweep(self):
        # Nothing above 7, a stop behind at 4, but d wants to go up from here: keep going up.
        assert next_heading(car(7, UP, pickups=[req("c", 4, 1), req("d", 7, 9)]), now=0) is UP

    def test_rule4_stops_only_behind_reverse(self):
        assert next_heading(car(5, UP, pickups=[req("p", 3, 1)]), now=0) is DOWN

    def test_rule4_opposite_pickup_here_does_not_stop_the_reversal(self):
        assert next_heading(car(7, UP, pickups=[req("c", 4, 1), req("d", 7, 2)]), now=0) is DOWN

    def test_rule5_idle_heading_takes_nearest_stop_lower_on_ties(self):
        assert next_heading(car(5, IDLE, pickups=[req("u", 7, 9), req("d", 3, 1)]), now=0) is DOWN
        assert next_heading(car(5, IDLE, pickups=[req("u", 6, 9), req("d", 3, 1)]), now=0) is UP

    def test_rule6_nothing_elsewhere_follows_earliest_pickup_here(self):
        assert next_heading(car(5, UP, pickups=[req("d", 5, 2), req("u", 5, 8)]), now=0) is DOWN

    def test_rule7_nothing_at_all_is_idle(self):
        assert next_heading(car(5, UP), now=0) is IDLE


class TestBoarding:
    def test_filters_by_floor_and_direction(self):
        c = car(5, pickups=[req("u", 5, 8), req("d", 5, 2), req("x", 7, 1)])
        assert [p.id for p in boarding(c, UP, now=0)] == ["u"]

    def test_caps_at_free_capacity_in_assignment_order(self):
        c = car(5, capacity=2, riders=[req("r", 1, 9)], pickups=[req("a", 5, 7), req("b", 5, 8)])
        assert [p.id for p in boarding(c, UP, now=0)] == ["a"]

    def test_idle_boards_nobody(self):
        assert boarding(car(5, pickups=[req("u", 5, 8)]), IDLE, now=0) == []


class TestStarvedRiderKeepsASeat:
    """``s`` (floor 2, asked at tick 0) is the oldest pickup; two riders wait at floor 5."""

    @staticmethod
    def two_seat_car(starve_after):
        return car(
            5,
            capacity=2,
            starve_after=starve_after,
            pickups=[req("s", 2, 1), req("a", 5, 8, time=50), req("b", 5, 9, time=51)],
        )

    def test_the_limit_is_six_round_trips_of_two_ticks_per_floor(self):
        assert starve_limit(BuildingConfig(num_elevators=1, num_floors=60, capacity=8)) == 720

    def test_overdue_is_the_oldest_pickup_once_it_has_waited_the_limit(self):
        c = self.two_seat_car(100)
        assert overdue(c, 99) is None
        assert overdue(c, 100).id == "s"

    def test_no_threshold_or_no_pickups_is_never_overdue(self):
        assert overdue(self.two_seat_car(None), 10_000) is None
        assert overdue(car(5, starve_after=0), 10_000) is None

    def test_below_the_limit_every_seat_is_offered(self):
        assert [p.id for p in boarding(self.two_seat_car(100), UP, now=99)] == ["a", "b"]

    def test_past_the_limit_one_seat_is_kept_for_the_starved_rider(self):
        assert [p.id for p in boarding(self.two_seat_car(100), UP, now=100)] == ["a"]

    def test_a_one_seat_car_boards_nobody_else(self):
        c = self.two_seat_car(100)
        c.capacity = 1
        assert boarding(c, UP, now=100) == []

    def test_the_tick_must_be_given_so_the_rule_cannot_be_switched_off_by_omission(self):
        # With a default of 0 this car, at tick 5,000, would offer both seats and ignore "s".
        c = self.two_seat_car(100)
        with pytest.raises(TypeError):
            boarding(c, UP)
        with pytest.raises(TypeError):
            next_heading(c)
        assert [p.id for p in boarding(c, UP, now=5_000)] == ["a"]

    def test_a_one_seat_car_turns_back_for_the_starved_rider_instead_of_freezing(self):
        # Heading UP on floor 5 with nothing above, "s" starved below and "a" here going up.
        # The only seat is kept for "s", so "a" cannot board and no stop appears ahead:
        # extending the sweep (rule 3) would leave the car on floor 5 for good.
        c = car(
            5, UP, capacity=1, starve_after=100, pickups=[req("s", 2, 1), req("a", 5, 8, time=50)]
        )
        assert next_heading(c, now=99) is UP
        assert next_heading(c, now=100) is DOWN
        assert plan_car(c, 100) == (DOWN, [], 2)
        # 3 ticks down to "s", 1 to floor 1, then back up 4 for "a" and 3 more to floor 8.
        assert simulate(c, now=100, stop_time=0) == {"s": 104, "a": 111}

    def test_the_starved_rider_boards_first_on_its_own_floor(self):
        # Car on floor 2 heading down, one seat: "s" is oldest, so it takes the seat.
        c = car(2, capacity=1, starve_after=10, pickups=[req("s", 2, 1), req("t", 2, 1, time=5)])
        assert [p.id for p in boarding(c, DOWN, now=10)] == ["s"]

    def test_the_replay_still_delivers_everyone(self):
        # Two seats, starved "s" on floor 2 going down, a queue at floor 5 going down too.
        pickups = [req("s", 2, 1)] + [req(f"q{i}", 5, 1, time=1) for i in range(6)]
        etas = simulate(car(1, capacity=2, pickups=pickups, starve_after=0), 0, 0)
        assert set(etas) == {p.id for p in pickups}


class TestNextTarget:
    def test_nearest_ahead_including_boarded_destinations(self):
        u = req("u", 5, 8)
        assert next_target(car(5, pickups=[u, req("f", 9, 1)]), UP, [u]) == 8

    def test_down_takes_the_highest_stop_below(self):
        assert next_target(car(5, riders=[req("r", 9, 2)], pickups=[req("p", 4, 1)]), DOWN, []) == 4

    def test_idle_is_none(self):
        assert next_target(car(5), IDLE, []) is None


def test_plan_car_boards_and_targets_in_one_call():
    u = req("u", 5, 8)
    assert plan_car(car(5, IDLE, pickups=[u]), 0) == (UP, [u], 8)


class TestSimulate:
    def test_single_rider_and_input_is_untouched(self):
        c = car(1, UP, riders=[req("a", 1, 5)])
        assert simulate(c, now=0, stop_time=0) == {"a": 4}
        assert (c.floor, len(c.riders)) == (1, 1)

    def test_pickup_then_dropoff(self):
        # t0 1->2, t1 ->3, t2 at 3 board (rule 6), t3 4, t4 5, t5 at 6: drop.
        assert simulate(car(1, pickups=[req("a", 3, 6)]), now=0, stop_time=0) == {"a": 5}

    def test_wrong_direction_passenger_waits_for_the_return_pass(self):
        # a boards at t0 heading up; b (down) is skipped at t2; a off at 6 (t5); reverse;
        # b boards at 3 (t8, rule 6 keeps DOWN); off at 1 (t10).
        etas = simulate(car(1, pickups=[req("a", 1, 6), req("b", 3, 1)]), now=0, stop_time=0)
        assert etas == {"a": 5, "b": 10}

    def test_stop_time_holds_the_car(self):
        # t2 at 3: a off (eta 2), hold to 4; b boards same tick (rule 6); t4 move; t6 at 5: b off.
        etas = simulate(
            car(1, UP, riders=[req("a", 1, 3)], pickups=[req("b", 3, 5)]), now=0, stop_time=2
        )
        assert etas == {"a": 2, "b": 6}

    def test_capacity_overflow_is_deferred_to_the_next_pass(self):
        # capacity 1: a boards t0, off at 3 (t2); reverse to 1 (t4), b boards; off at 3 (t6).
        etas = simulate(
            car(1, capacity=1, pickups=[req("a", 1, 3), req("b", 1, 3)]), now=0, stop_time=0
        )
        assert etas == {"a": 2, "b": 6}

    def test_boarded_at_records_each_pickups_boarding_tick(self):
        # Same run as the return-pass test: a boards t0, b boards on the way back at t8.
        boarded: dict[str, int] = {}
        simulate(
            car(1, pickups=[req("a", 1, 6), req("b", 3, 1)]), now=0, stop_time=0, boarded_at=boarded
        )
        assert boarded == {"a": 0, "b": 8}

    def test_initial_hold_is_respected_and_now_offsets_ticks(self):
        # held 2 ticks from t10: moves at t12 and t13, arrives for t14.
        assert simulate(car(1, UP, riders=[req("a", 1, 3)], held=2), now=10, stop_time=0) == {
            "a": 14
        }

    def test_a_backlog_longer_than_100_000_ticks_is_replayed_to_the_end(self):
        # One seat and 2,000 riders each crossing 40 floors: about 160,000 ticks of work.
        # The guard is sized from the workload, so a long drain is not a "runaway".
        pickups = [req(f"p{i}", 1, 41) for i in range(2000)]
        etas = simulate(car(1, capacity=1, pickups=pickups), now=0, stop_time=0)
        assert len(etas) == 2000
        assert max(etas.values()) > 100_000

    def test_an_explicit_max_ticks_still_raises(self):
        with pytest.raises(ReplayError):
            simulate(car(1, pickups=[req("a", 1, 41)]), now=0, stop_time=0, max_ticks=10)


class TestReplayAgreesWithTheEngine:
    """The one place two implementations of the same rules can be cross-checked.

    ``simulate`` re-implements four engine rules -- the alight test, the phase order, the
    hold rule and the movement gate -- and the cost schedulers rank every assignment on its
    output. Nothing else in the suite asserts the two agree, so a change to ``_hold``,
    ``_move`` or the tick order could bias every ETA silently.
    """

    @staticmethod
    def requests(seed, n=8, floors=12):
        """All at tick 0, so once they are bound the car's future is fully determined."""
        rng = random.Random(seed)
        out = []
        for i in range(n):
            source = rng.randint(1, floors)
            dest = rng.choice([f for f in range(1, floors + 1) if f != source])
            out.append(Request(0, f"p{i}", source, dest))
        return out

    @pytest.mark.parametrize("round_trips", [controller.STARVE_ROUND_TRIPS, 0])
    @pytest.mark.parametrize("stop_time", [0, 2])
    @pytest.mark.parametrize("seed", range(6))
    def test_the_replay_predicts_the_engines_actual_dropoffs(
        self, seed, stop_time, round_trips, monkeypatch
    ):
        # 0 round trips starves every oldest pickup at once, so the seat rule runs throughout.
        monkeypatch.setattr(controller, "STARVE_ROUND_TRIPS", round_trips)
        requests = self.requests(seed)
        config = BuildingConfig(num_elevators=1, num_floors=12, capacity=3, stop_time=stop_time)
        scheduler = DestinationDispatch(EtaCost())
        sim = Simulation(config, scheduler, requests)

        # One tick binds every request and no more arrive, so from here the replay is a
        # prediction of the whole run rather than of a moving target.
        sim.step()
        snapshot = from_view(
            sim.state.elevators[0],
            scheduler.heading[0],
            scheduler.assigned[0],
            sim.time,
            starve_after=starve_limit(config),
        )
        boarded: dict[str, int] = {}
        predicted = simulate(snapshot, sim.time, config.stop_time, boarded_at=boarded)

        result = sim.run(max_ticks=10_000)
        actual = {p.request.id: p.dropoff_time for p in result.passengers}
        picked = {p.request.id: p.pickup_time for p in result.passengers}
        assert predicted == {i: actual[i] for i in predicted}
        assert boarded == {i: picked[i] for i in boarded}
        assert set(boarded) == {p.id for p in snapshot.pickups}
        assert set(predicted) == set(actual), "the replay should account for every request"
