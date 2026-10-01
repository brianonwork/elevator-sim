import pytest
from conftest import IdleScheduler

from elevator_sim.algorithms import BUILTINS, available
from elevator_sim.models import BuildingConfig
from elevator_sim.scheduler import CarAction


def test_car_action_defaults_to_stay_and_board_nobody():
    action = CarAction()
    assert action.target is None
    assert action.board == ()


def test_public_api_creates_a_scheduler_without_importing_the_subpackage():
    import elevator_sim

    config = BuildingConfig(num_elevators=2, num_floors=10, capacity=4)
    assert "eta-cost" in elevator_sim.available()
    assert elevator_sim.create("eta-cost", config) is not None


def test_unknown_scheduler_names_the_available_ones():
    import elevator_sim

    config = BuildingConfig(num_elevators=1, num_floors=5, capacity=2)
    with pytest.raises(KeyError, match="eta-cost"):
        elevator_sim.create("nope", config)


def test_available_is_sorted(monkeypatch):
    # Inserted out of alphabetical order: a list() of BUILTINS would put "zzz-last" before
    # "-first", since dicts preserve insertion order. Only sorted() gets this right.
    monkeypatch.setitem(BUILTINS, "zzz-last", lambda cfg: IdleScheduler())
    monkeypatch.setitem(BUILTINS, "-first", lambda cfg: IdleScheduler())
    assert available() == [
        "-first",
        "eta-cost",
        "eta-cost-fair",
        "express",
        "nearest-car",
        "nearest-car-eta",
        "round-robin",
        "zone-based",
        "zzz-last",
    ]
