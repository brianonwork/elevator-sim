import dataclasses

import pytest
from conftest import IdleScheduler, ScriptedScheduler

from elevator_sim.models import BuildingConfig, Elevator, ElevatorView, Request, SimulationState
from elevator_sim.scheduler import CarAction, SchedulingError
from elevator_sim.simulation import Simulation, SimulationResult, SimulationStalled


def cfg(elevators=1, floors=10, capacity=8, **kwargs):
    return BuildingConfig(num_elevators=elevators, num_floors=floors, capacity=capacity, **kwargs)


# -- basic service -------------------------------------------------------------------


def test_single_passenger_is_picked_up_and_dropped_off(naive_scheduler):
    sim = Simulation(cfg(), naive_scheduler, [Request(0, "p1", 1, 5)])
    result = sim.run()

    assert isinstance(result, SimulationResult)
    [p] = result.passengers
    assert p.elevator_id == 0
    assert p.pickup_time == 0
    assert p.dropoff_time == 4
    assert p.wait_time == 0 and p.travel_time == 4 and p.total_time == 4


def test_positions_log_starts_at_zero_and_records_every_tick(naive_scheduler):
    sim = Simulation(cfg(), naive_scheduler, [Request(0, "p1", 1, 5)])
    result = sim.run()

    assert result.positions_log == [(0, (1,)), (1, (2,)), (2, (3,)), (3, (4,)), (4, (5,))]
    assert result.ticks == 5


def test_passenger_waiting_at_elevator_floor_boards_same_tick(naive_scheduler):
    sim = Simulation(cfg(), naive_scheduler, [Request(2, "p", 1, 3)])
    sim.run()
    assert sim.passengers[0].pickup_time == 2


def test_capacity_leaves_overflow_waiting_but_still_served(naive_scheduler):
    requests = [Request(0, f"p{i}", 1, 3) for i in range(3)]
    sim = Simulation(cfg(capacity=2), naive_scheduler, requests)
    sim.step()  # t=0: two board, one keeps waiting
    car = sim.elevators[0]
    assert [p.request.id for p in car.riders] == ["p0", "p1"]
    assert [r.id for r in sim.state.waiting] == ["p2"]

    result = sim.run()
    by_id = {p.request.id: p for p in result.passengers}
    assert by_id["p0"].dropoff_time == 2
    assert by_id["p2"].pickup_time == 4  # car returns 3 -> 1 after dropping the first two
    assert by_id["p2"].dropoff_time == 6


def test_car_moves_one_floor_per_tick_toward_its_target(naive_scheduler):
    sim = Simulation(cfg(), naive_scheduler, [Request(0, "down", 3, 1), Request(0, "x", 1, 2)])
    car = sim.elevators[0]
    assert car.floor == 1
    sim.step()  # t=0: boards x at floor 1, moves toward 2
    assert car.floor == 2
    sim.step()  # t=1: drops x at 2, heads for 3
    assert car.floor == 3
    sim.step()  # t=2: boards "down" at 3, moves toward 1
    assert car.floor == 2
    sim.run()
    assert car.floor == 1  # final tick: unload at 1, nowhere to go


def test_run_with_no_requests_logs_time_zero_only(naive_scheduler):
    result = Simulation(cfg(elevators=2), naive_scheduler, []).run()
    assert result.positions_log == [(0, (1, 1))]
    assert result.passengers == []


def test_constructor_rejects_requests_outside_building(naive_scheduler):
    with pytest.raises(ValueError):
        Simulation(cfg(floors=5), naive_scheduler, [Request(0, "p", 1, 9)])


def test_stalled_simulation_raises_after_max_ticks():
    sim = Simulation(cfg(), IdleScheduler(), [Request(0, "stuck", 2, 3)])
    with pytest.raises(SimulationStalled, match="stuck"):
        sim.run(max_ticks=20)
    assert sim.time == 20


def test_a_stall_names_requests_that_were_never_released():
    sim = Simulation(cfg(), IdleScheduler(), [Request(10, "late", 2, 3)])
    with pytest.raises(SimulationStalled, match=r"1 request\(s\) not yet released"):
        sim.run(max_ticks=5)


# -- request release and the snapshot ---------------------------------------------------


def test_requests_are_not_visible_before_their_time(naive_scheduler):
    sim = Simulation(cfg(), naive_scheduler, [Request(10, "late", 3, 6)])
    for _ in range(10):
        assert sim.state.waiting == ()
        sim.step()
    assert sim.time == 10
    sim.step()
    assert [p.request.id for p in sim.passengers] == ["late"]


def test_requests_out_of_order_in_input_are_released_by_time(naive_scheduler):
    sim = Simulation(cfg(), naive_scheduler, [Request(3, "b", 2, 4), Request(0, "a", 1, 2)])
    sim.step()
    assert [p.request.id for p in sim.passengers] == ["a"]


def test_state_new_lists_this_ticks_requests_and_waiting_includes_them():
    seen = []

    def record(state):
        seen.append((state.time, [r.id for r in state.new], [r.id for r in state.waiting]))
        return {}

    requests = [Request(0, "a", 4, 2), Request(0, "b", 6, 1), Request(2, "c", 3, 5)]
    sim = Simulation(cfg(), ScriptedScheduler(record), requests)
    for _ in range(3):
        sim.step()
    assert seen == [
        (0, ["a", "b"], ["a", "b"]),
        (1, [], ["a", "b"]),
        (2, ["c"], ["a", "b", "c"]),
    ]


def test_scheduler_is_called_once_per_tick_with_one_snapshot():
    snapshots = []

    def record(state):
        snapshots.append(state)
        return {}

    sim = Simulation(cfg(elevators=3), ScriptedScheduler(record), [])
    sim.step()
    sim.step()
    assert [s.time for s in snapshots] == [0, 1]
    assert all(isinstance(s, SimulationState) for s in snapshots)
    assert all(len(s.elevators) == 3 for s in snapshots)


def test_snapshot_is_frozen_and_detached_from_engine_objects():
    captured = []
    sim = Simulation(
        cfg(), ScriptedScheduler(lambda s: captured.append(s) or {}), [Request(0, "a", 1, 5)]
    )
    sim.step()
    [state] = captured
    view = state.elevators[0]
    assert isinstance(view, ElevatorView)
    assert not isinstance(view, Elevator)
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.floor = 9
    with pytest.raises(AttributeError):
        state.waiting[0].source = 9  # Request is frozen too
    sim.elevators[0].floor = 7
    assert view.floor == 1


def test_free_car_reports_held_until_equal_to_now():
    seen = []
    sim = Simulation(
        cfg(), ScriptedScheduler(lambda s: seen.append(s.elevators[0].held_until) or {}), []
    )
    sim.step()
    sim.step()
    assert seen == [0, 1]


def test_view_riders_reflect_boarded_passengers_after_alighting():
    seen = []

    def plan(state):
        seen.append([(r.request.id, r.pickup_time) for r in state.elevators[0].riders])
        board = tuple(r.id for r in state.waiting if r.source == state.elevators[0].floor)
        return {0: CarAction(target=3, board=board)}

    sim = Simulation(cfg(), ScriptedScheduler(plan), [Request(0, "a", 1, 3), Request(0, "b", 1, 2)])
    sim.run()
    # t0: nobody aboard yet; t1: b already alighted at 2 before the plan; t2: a alighted at 3
    assert seen == [[], [("a", 0)], []]


# -- boarding is entirely the scheduler's call -----------------------------------------


def test_empty_plan_leaves_every_car_idle_and_nobody_boards():
    sim = Simulation(cfg(elevators=2), IdleScheduler(), [Request(0, "a", 1, 3)])
    sim.step()
    assert [e.floor for e in sim.elevators] == [1, 1]
    assert sim.passengers[0].pickup_time is None


def test_scheduler_boards_a_subset_and_the_rest_keep_waiting():
    sim = Simulation(
        cfg(), ScriptedScheduler(lambda s: {0: CarAction(board=("b",))}),
        [Request(0, "a", 1, 3), Request(0, "b", 1, 4)],
    )
    sim.step()
    by_id = {p.request.id: p for p in sim.passengers}
    assert by_id["b"].pickup_time == 0 and by_id["b"].elevator_id == 0
    assert by_id["a"].pickup_time is None and by_id["a"].elevator_id is None
    assert [r.id for r in sim.state.waiting] == ["a"]


def test_boarding_order_follows_the_plan():
    seen = []

    def plan(state):
        if state.time == 0:
            return {0: CarAction(board=("b", "a"))}
        seen.append([r.request.id for r in state.elevators[0].riders])
        return {}

    sim = Simulation(cfg(), ScriptedScheduler(plan), [Request(0, "a", 1, 3), Request(0, "b", 1, 4)])
    sim.step()
    sim.step()
    assert seen == [["b", "a"]]


def test_any_car_may_board_a_waiting_passenger():
    def plan(state):
        car = state.elevators[1]
        board = tuple(r.id for r in state.waiting if r.source == car.floor)
        return {1: CarAction(board=board, target=3)}

    sim = Simulation(cfg(elevators=2), ScriptedScheduler(plan), [Request(0, "a", 1, 3)])
    sim.run()
    assert sim.passengers[0].elevator_id == 1
    assert sim.passengers[0].dropoff_time == 2


def test_alighting_happens_before_the_plan_so_freed_seats_can_be_reused():
    def plan(state):
        car = state.elevators[0]
        board = tuple(r.id for r in state.waiting if r.source == car.floor)[: car.free_capacity]
        return {0: CarAction(target=5, board=board)}

    sim = Simulation(
        cfg(capacity=1), ScriptedScheduler(plan), [Request(0, "a", 1, 3), Request(0, "b", 3, 5)]
    )
    sim.run()
    by_id = {p.request.id: p for p in sim.passengers}
    assert by_id["a"].dropoff_time == 2
    assert by_id["b"].pickup_time == 2  # boards the tick a gets off
    assert by_id["b"].dropoff_time == 4


# -- constraint enforcement ------------------------------------------------------------


@pytest.mark.parametrize(
    "plan,match",
    [
        ({7: CarAction()}, "valid ids are"),
        ({0: CarAction(board=("ghost",))}, "no waiting passenger 'ghost'"),
        ({0: CarAction(board=("far",))}, "is waiting on floor 5"),
        ({0: CarAction(board=("a", "a"))}, "already boarded onto elevator"),
        ({0: CarAction(target=99)}, "building has floors 1..10"),
        ({0: CarAction(target=0)}, "building has floors 1..10"),
    ],
)
def test_invalid_plan_raises_scheduling_error(plan, match):
    requests = [Request(0, "a", 1, 3), Request(0, "far", 5, 3)]
    sim = Simulation(cfg(), ScriptedScheduler(lambda s: plan), requests)
    with pytest.raises(SchedulingError, match=match):
        sim.step()


def test_same_passenger_in_two_cars_raises():
    sim = Simulation(
        cfg(elevators=2),
        ScriptedScheduler(lambda s: {0: CarAction(board=("a",)), 1: CarAction(board=("a",))}),
        [Request(0, "a", 1, 3)],
    )
    with pytest.raises(SchedulingError, match="a"):
        sim.step()


def test_a_rejected_plan_boards_nobody():
    sim = Simulation(
        cfg(elevators=2, floors=5),
        ScriptedScheduler(lambda s: {0: CarAction(board=("a",)),
                                     1: CarAction(board=("ghost",))}),
        [Request(0, "a", 1, 4)],
    )
    with pytest.raises(SchedulingError, match="ghost"):
        sim.step()
    assert sim.elevators[0].riders == []
    assert sim.waiting == 1
    assert sim.passengers[0].pickup_time is None


def test_boarding_beyond_capacity_raises():
    requests = [Request(0, f"p{i}", 1, 3) for i in range(3)]
    sim = Simulation(
        cfg(capacity=2),
        ScriptedScheduler(lambda s: {0: CarAction(board=("p0", "p1", "p2"))}),
        requests,
    )
    with pytest.raises(SchedulingError, match="capacity"):
        sim.step()


def test_any_car_may_board_anyone_at_its_floor():
    # The engine has no served floors: a scripted plan may put a lobby rider on car 1.
    config = cfg(elevators=2)

    def plan(state):
        return {1: CarAction(board=tuple(r.id for r in state.waiting if r.source == 1), target=5)}

    sim = Simulation(config, ScriptedScheduler(plan), [Request(0, "a", 1, 5)])
    sim.run()
    assert sim.passengers[0].elevator_id == 1 and sim.passengers[0].dropoff_time == 4


def test_target_equal_to_current_floor_means_idle():
    sim = Simulation(cfg(), ScriptedScheduler(lambda s: {0: CarAction(target=1)}), [])
    sim.step()
    assert sim.elevators[0].floor == 1


# -- stop time -------------------------------------------------------------------------


def test_stop_time_holds_the_car_after_boarding_and_target_is_ignored_meanwhile():
    held = []

    def plan(state):
        held.append(state.elevators[0].held_until)
        return {0: CarAction(target=5, board=tuple(r.id for r in state.waiting if r.source == 1))}

    sim = Simulation(cfg(stop_time=2), ScriptedScheduler(plan), [Request(0, "a", 1, 3)])
    for _ in range(4):
        sim.step()
    assert [floors[0] for _, floors in sim.positions_log] == [1, 1, 1, 2]
    assert held == [0, 2, 2, 3]


def test_stop_time_holds_the_car_after_alighting():
    def plan(state):
        car = state.elevators[0]
        board = tuple(r.id for r in state.waiting if r.source == car.floor)
        return {0: CarAction(target=5, board=board)}

    sim = Simulation(
        cfg(stop_time=1), ScriptedScheduler(plan),
        [Request(0, "a", 1, 3), Request(0, "b", 1, 5)],
    )
    result = sim.run()
    by_id = {p.request.id: p for p in result.passengers}
    # t0 board (hold 1), t1 held, t2 at 2, t3 at 3 a alights (hold 1), t4 held, t5 at 4, t6 at 5
    assert by_id["a"].dropoff_time == 3
    assert by_id["b"].dropoff_time == 6
    assert [floors[0] for _, floors in result.positions_log] == [1, 1, 2, 3, 3, 4, 5]


def test_zero_stop_time_keeps_stops_free(naive_scheduler):
    sim = Simulation(
        cfg(stop_time=0), naive_scheduler, [Request(0, "a", 1, 3), Request(0, "b", 1, 5)]
    )
    result = sim.run()
    assert result.ticks == 5


def test_until_stops_at_the_horizon_leaving_the_rest_unserved(naive_scheduler):
    # 'a' is dropped at floor 3 on tick 2; 'b' is still riding when the horizon cuts in.
    sim = Simulation(cfg(), naive_scheduler, [Request(0, "a", 1, 3), Request(0, "b", 1, 9)])
    result = sim.run(until=4)
    by_id = {p.request.id: p for p in result.passengers}
    assert result.ticks == 4
    assert by_id["a"].is_done and by_id["b"].dropoff_time is None


def test_until_does_not_raise_where_max_ticks_would(naive_scheduler):
    requests = [Request(0, "a", 1, 9)]
    assert Simulation(cfg(), naive_scheduler, requests).run(until=3).ticks == 3
    with pytest.raises(SimulationStalled):
        Simulation(cfg(), naive_scheduler, requests).run(max_ticks=3)


def test_until_zero_still_logs_time_zero(naive_scheduler):
    # The docstring promises "Always logs at least time 0"; the until break was checked first,
    # so until=0 returned an empty log. One tick runs, which is what logging time 0 costs.
    result = Simulation(cfg(), naive_scheduler, [Request(0, "a", 1, 3)]).run(until=0)
    assert result.positions_log == [(0, (1,))]
    assert result.ticks == 1


def test_a_rejected_plan_stops_the_simulation_rather_than_leaving_a_tick_half_done():
    # Tick 0 has been logged and its request released by the time the plan is rejected. A
    # second step would log tick 0 again, so the engine refuses it instead.
    sim = Simulation(cfg(), ScriptedScheduler(lambda state: {0: CarAction(board=("ghost",))}),
                     [Request(0, "a", 1, 3)])
    with pytest.raises(SchedulingError, match="ghost"):
        sim.step()
    with pytest.raises(SchedulingError, match="stopped at tick 0.*ghost"):
        sim.step()
    assert [t for t, _ in sim.positions_log] == [0]
    assert len(sim.passengers) == 1
