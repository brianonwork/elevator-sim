"""The evaluation set builds for every building, and its anchor floors scale with height."""

import pytest

from elevator_sim.trials.presets import (
    BUILDINGS,
    BY_NAME,
    BY_PRESET_NAME,
    DEMAND,
    EXPERIMENTS,
    OVERLOAD_DEMAND,
    PRESETS,
    big_bang_and_stop_time_pair,
    big_bang_variants,
    config_for,
    hub,
    rate_for,
    top_band,
)
from elevator_sim.trials.traffic import generate

B1 = BY_NAME["B1"]


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.name)
@pytest.mark.parametrize("building", BUILDINGS, ids=lambda b: b.name)
def test_every_preset_generates_valid_traffic_on_every_building(preset, building):
    bound = {"horizon": 200} if preset.horizon else {"count": 40}
    requests = generate(
        preset.mixture(building), preset.arrival(0.3, 400), building.floors,
        preset.seeds(building)[0], **bound,
    )
    assert requests
    assert all(1 <= r.source <= building.floors for r in requests)
    assert all(1 <= r.dest <= building.floors for r in requests)


def test_nominal_throughput_is_one_rider_per_seat_per_round_trip():
    # 4 cars x 8 seats every 120 ticks (60 floors up and back).
    assert B1.nominal_rate == pytest.approx(32 / 120)
    assert BY_NAME["B2"].nominal_rate == pytest.approx(16 / 120)   # half the cars
    assert BY_NAME["B4"].nominal_rate == pytest.approx(32 / 60)    # half the height
    assert BY_NAME["B7"].nominal_rate == pytest.approx(64 / 120)   # double the seats


def test_the_arrival_rate_is_demand_times_nominal_throughput():
    baseline, overload = BY_PRESET_NAME["baseline"], BY_PRESET_NAME["overload"]
    assert rate_for(baseline, B1) == pytest.approx(0.2)
    assert rate_for(overload, B1) == pytest.approx(0.4)
    assert rate_for(baseline, BY_NAME["B2"]) == pytest.approx(0.1)
    assert rate_for(baseline, BY_NAME["B5"]) == pytest.approx(0.12)
    # Fixed across patterns: every standard preset runs at the same demand.
    assert {p.load for p in PRESETS if p.name != "overload"} == {DEMAND}
    assert overload.load == OVERLOAD_DEMAND == 2 * DEMAND


def test_overload_is_skewed_two_way_traffic_at_a_higher_demand():
    # Same mixture and arrival shape, so the same rate, seed and bound give identical
    # requests; only the demand and the tick horizon differ.
    skewed, overload = BY_PRESET_NAME["skewed-two-way"], BY_PRESET_NAME["overload"]
    b1 = BY_NAME["B1"]
    assert overload.load > skewed.load and overload.horizon and not skewed.horizon
    skewed_requests = generate(
        skewed.mixture(b1), skewed.arrival(0.3, 400), b1.floors, seed=0, count=40,
    )
    overload_requests = generate(
        overload.mixture(b1), overload.arrival(0.3, 400), b1.floors, seed=0, count=40,
    )
    assert skewed_requests == overload_requests


def test_anchor_floors_scale_with_the_building():
    assert (hub(60), top_band(60)) == (30, (41, 60))  # the reference building's own numbers
    assert (hub(30), top_band(30)) == (15, (21, 30))
    assert (hub(100), top_band(100)) == (50, (67, 100))
    assert hub(61) == 31  # odd floor count: the true middle


def test_seeds_are_reproducible_and_never_collide_between_cells():
    baseline, morning = PRESETS[0], PRESETS[1]
    assert baseline.seeds(B1) == baseline.seeds(B1)      # every scheduler gets this list
    assert len(set(baseline.seeds(B1))) == B1.seeds == 10
    assert not set(baseline.seeds(B1)) & set(morning.seeds(B1))
    assert not set(baseline.seeds(B1)) & set(baseline.seeds(BY_NAME["B2"]))


class TestExperimentVariants:
    def test_every_preset_runs_both_big_bang_experiments_on_every_building(self):
        for preset in PRESETS:
            for building in BUILDINGS:
                names = [v.name for v in preset.variants(building)]
                assert names[:2] == list(EXPERIMENTS.values()) == ["plain", "stop1"]

    def test_big_bang_differs_only_in_stop_time(self):
        assert [v.stop_time for v in big_bang_variants(B1)] == [0, 1]

    def test_the_3_tick_run_is_on_the_reference_building_only(self):
        assert [v.name for v in big_bang_and_stop_time_pair(B1)] == ["plain", "stop1", "stop3"]
        assert [v.name for v in big_bang_and_stop_time_pair(BY_NAME["B4"])] == ["plain", "stop1"]

    def test_the_variants_differ_only_in_stop_time(self):
        plain, stop1, stop3 = (config_for(B1, v) for v in big_bang_and_stop_time_pair(B1))
        assert (plain.stop_time, stop1.stop_time, stop3.stop_time) == (0, 1, 3)
        assert (plain.num_elevators, plain.num_floors, plain.capacity) == (4, 60, 8)
        assert not hasattr(plain, "served_floors")


def test_seeds_are_pinned_and_do_not_depend_on_where_a_building_sits_in_the_list(monkeypatch):
    # runs.csv is keyed by these seeds, so they must survive a building being added or moved.
    from elevator_sim.trials import presets

    expected = {
        (p.number, b.name): [p.number * 10_000 + block * 100 + i for i in range(10)]
        for p in presets.PRESETS
        for block, b in enumerate(presets.BUILDINGS)
    }
    assert expected[(2, "B1")][0] == 20_000 and expected[(8, "B7")][9] == 80_609
    monkeypatch.setattr(presets, "BUILDINGS", tuple(reversed(presets.BUILDINGS)))
    for p in presets.PRESETS:
        for b in presets.BUILDINGS:
            assert p.seeds(b) == expected[(p.number, b.name)]
