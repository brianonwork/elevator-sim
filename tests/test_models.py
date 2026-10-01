import dataclasses

import pytest

from elevator_sim.models import (
    BuildingConfig,
    Direction,
    Elevator,
    ElevatorView,
    Passenger,
    Request,
    Rider,
    SimulationState,
    validate_requests,
)


def make_passenger(time=0, id="p", source=1, dest=5):
    return Passenger(Request(time=time, id=id, source=source, dest=dest))


class TestDirection:
    def test_toward_is_idle_only_when_the_floors_match(self):
        assert Direction.toward(1, 5) is Direction.UP
        assert Direction.toward(5, 1) is Direction.DOWN
        assert Direction.toward(3, 3) is Direction.IDLE


class TestRequest:
    def test_direction_follows_source_and_dest(self):
        assert Request(0, "p", 1, 5).direction is Direction.UP
        assert Request(0, "p", 5, 1).direction is Direction.DOWN

    def test_rejects_same_source_and_dest(self):
        with pytest.raises(ValueError):
            Request(time=0, id="p", source=3, dest=3)

    def test_rejects_non_positive_floor(self):
        with pytest.raises(ValueError):
            Request(time=0, id="p", source=0, dest=3)

    def test_rejects_negative_time(self):
        with pytest.raises(ValueError):
            Request(time=-1, id="p", source=1, dest=3)


class TestBuildingConfig:
    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(num_elevators=0, num_floors=10, capacity=4),
            dict(num_elevators=1, num_floors=1, capacity=4),
            dict(num_elevators=1, num_floors=10, capacity=0),
            dict(num_elevators=1, num_floors=10, capacity=1, stop_time=-1),
        ],
    )
    def test_rejects_bad_values(self, kwargs):
        with pytest.raises(ValueError):
            BuildingConfig(**kwargs)

    def test_has_no_served_floors(self):
        # Express is a scheduler's rule, not a fleet setting; the engine knows every car
        # may stop anywhere.
        assert not hasattr(BuildingConfig(num_elevators=2, num_floors=10, capacity=1),
                           "served_floors")


class TestPassenger:
    def test_timestamps_accumulate_through_request_pickup_and_dropoff(self):
        p = make_passenger(time=2)
        assert (p.wait_time, p.travel_time, p.total_time) == (None, None, None)
        p.pickup_time = 5
        assert (p.wait_time, p.travel_time, p.total_time) == (3, None, None)
        p.dropoff_time = 9
        assert (p.wait_time, p.travel_time, p.total_time) == (3, 4, 7)
        assert p.is_done


class TestElevator:
    def test_view_is_a_frozen_copy(self):
        e = Elevator(id=3, capacity=2, floor=7, held_until=9)
        p = make_passenger(id="r", source=1, dest=8)
        p.pickup_time = 4
        e.riders.append(p)
        view = e.view()
        assert isinstance(view, ElevatorView)
        assert (view.id, view.floor, view.capacity, view.held_until) == (3, 7, 2, 9)
        assert view.riders == (Rider(request=p.request, pickup_time=4),)
        with pytest.raises(dataclasses.FrozenInstanceError):
            view.floor = 1
        e.floor = 1
        assert view.floor == 7


class TestElevatorView:
    def view(self, capacity=2, riders=()):
        return ElevatorView(id=0, floor=1, capacity=capacity, riders=tuple(riders), held_until=0)

    def test_load_and_free_capacity(self):
        # `load` is what the policies read (policies.py:70, 90), and `free_capacity` is used by
        # the worked scheduler example in docs/write-up/2-code-architecture.md, so both are live
        # surface. `is_full`
        # has no caller anywhere and is deliberately left unasserted.
        r = Rider(Request(0, "a", 1, 2), pickup_time=0)
        v = self.view(capacity=2, riders=[r])
        assert v.load == 1 and v.free_capacity == 1


class TestSimulationState:
    def test_is_frozen(self):
        cfg = BuildingConfig(num_elevators=1, num_floors=5, capacity=1)
        state = SimulationState(time=0, config=cfg, elevators=(), waiting=(), new=())
        with pytest.raises(dataclasses.FrozenInstanceError):
            state.time = 1


class TestValidateRequests:
    def test_accepts_in_range_floors(self):
        cfg = BuildingConfig(num_elevators=1, num_floors=5, capacity=1)
        validate_requests([Request(0, "a", 1, 5)], cfg)  # no error

    @pytest.mark.parametrize("source,dest", [(1, 6), (6, 1)])
    def test_rejects_floor_above_building(self, source, dest):
        cfg = BuildingConfig(num_elevators=1, num_floors=5, capacity=1)
        with pytest.raises(ValueError, match="a"):
            validate_requests([Request(0, "a", source, dest)], cfg)

    def test_rejects_duplicate_ids(self):
        cfg = BuildingConfig(num_elevators=1, num_floors=5, capacity=1)
        with pytest.raises(ValueError, match="dup"):
            validate_requests([Request(0, "dup", 1, 2), Request(1, "dup", 2, 3)], cfg)

