"""The wait bound: a rider the sweep keeps passing over full still boards.

The scenario: ``victim`` asks from floor 3 down to 2 at tick 1, while riders asking from
floor 4 down to 2 arrive every tick. On every down pass the car fills at floor 4 before it
reaches floor 3, so without the seat rule the victim boards only once the stream stops.
"""

import pytest

from elevator_sim.algorithms import BUILTINS, controller, create
from elevator_sim.algorithms.controller import starve_limit
from elevator_sim.models import BuildingConfig, Request
from elevator_sim.simulation import Simulation

STREAM = 200
"""Ticks the floor-4 stream runs; the unbounded wait grows with it."""


def victim_wait(policy, elevators):
    config = BuildingConfig(num_elevators=elevators, num_floors=6, capacity=1)
    requests = [Request(1, "victim", 3, 2)]
    requests += [Request(t, f"s{t}", 4, 2) for t in range(STREAM)]
    result = Simulation(config, create(policy, config), requests).run(max_ticks=100_000)
    (victim,) = [p for p in result.passengers if p.request.id == "victim"]
    return victim.wait_time, config


@pytest.mark.parametrize("policy", sorted(set(BUILTINS) - {"express"}))
def test_without_the_rule_one_car_starves_the_rider_whatever_the_policy(policy, monkeypatch):
    # One car leaves no policy a choice; a huge limit switches the seat rule off.
    monkeypatch.setattr(controller, "STARVE_ROUND_TRIPS", 10**9)
    wait, _ = victim_wait(policy, elevators=1)
    assert wait > STREAM


@pytest.mark.parametrize("policy", sorted(BUILTINS))
@pytest.mark.parametrize("elevators", [1, 2])
def test_every_scheduler_boards_the_rider_within_the_limit_plus_a_few_sweeps(
    policy, elevators
):
    if policy == "express" and elevators == 1:
        pytest.skip("express needs two cars; its local car carries every trip here")
    wait, config = victim_wait(policy, elevators)
    # Once starved: riders aboard get off within one round trip, then the car passes floor
    # 3 going down within two more.
    assert wait <= starve_limit(config) + 3 * 2 * config.num_floors
