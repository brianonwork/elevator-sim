"""Traffic generator: shapes, arrival processes, determinism, and the tagged CSV."""

from random import Random

import pytest

from elevator_sim.csv_io import read_requests
from elevator_sim.trials.traffic import (
    Flow,
    band,
    bursts,
    generate,
    offset,
    pick,
    point,
    poisson,
    ramp,
    steady,
    uniform,
    write_requests,
)

LOBBY_UP = Flow("up", point(1), uniform())
TO_LOBBY = Flow("down", band(2, 60), point(1))
MIXED = [(0.5, LOBBY_UP), (0.5, TO_LOBBY)]


class TestDistributions:
    def test_shapes_stay_inside_their_range(self):
        rng = Random(0)
        assert {point(7)(rng, 60, None) for _ in range(5)} == {7}
        assert all(10 <= band(10, 20)(rng, 60, None) <= 20 for _ in range(50))
        assert all(1 <= uniform()(rng, 60, None) <= 60 for _ in range(50))

    def test_offset_is_a_short_hop_clamped_to_the_building(self):
        rng = Random(0)
        hops = [offset(1, 3)(rng, 60, 30) for _ in range(50)]
        assert all(27 <= f <= 33 for f in hops) and len(set(hops)) > 1
        assert all(offset(1, 3)(rng, 60, 1) >= 1 for _ in range(20))

    def test_destination_is_redrawn_off_the_source(self):
        rng = Random(0)
        assert all(s != d for s, d in (LOBBY_UP.sample(rng, 60) for _ in range(200)))

    def test_flow_with_nowhere_to_go_raises(self):
        with pytest.raises(ValueError, match="no destination"):
            Flow("stuck", point(5), point(5)).sample(Random(0), 60)


class TestArrivals:
    def test_steady_is_poisson_so_two_can_share_a_tick(self):
        rng = Random(3)
        counts = [steady(0.8)(rng, t) for t in range(400)]
        assert max(counts) >= 2 and 0 in counts

    def test_poisson_mean_is_about_right(self):
        rng = Random(4)
        assert 0.7 < sum(poisson(rng, 0.9) for _ in range(2000)) / 2000 < 1.1

    def test_bursts_land_on_the_period_and_nowhere_else(self):
        arrival = bursts(size=20, period=10)
        assert [arrival(Random(0), t) for t in range(21)].count(20) == 3
        assert arrival(Random(0), 5) == 0

    def test_ramp_climbs_over_its_window_then_holds(self):
        def mean_at(tick):
            # A fresh generator per call: sharing one made the result depend on the order
            # the assertions below happen to evaluate in.
            rng = Random(5)
            return sum(ramp(2.0, climb=100)(rng, tick) for _ in range(500)) / 500

        assert mean_at(5) < mean_at(30) < mean_at(90)
        assert mean_at(100) == pytest.approx(mean_at(400), rel=0.2)


class TestGenerate:
    def test_same_seed_reproduces_exactly_and_a_different_one_does_not(self):
        first = generate(MIXED, steady(0.3), 60, seed=1, count=50)
        assert first == generate(MIXED, steady(0.3), 60, seed=1, count=50)
        assert first != generate(MIXED, steady(0.3), 60, seed=2, count=50)

    def test_count_bound_emits_exactly_that_many_requests(self):
        requests = generate(MIXED, steady(0.3), 60, seed=1, count=50)
        assert len(requests) == 50
        assert [r.id for r in requests] == [f"p{i:05d}" for i in range(50)]

    def test_horizon_bound_stops_at_the_last_tick(self):
        requests = generate(MIXED, steady(0.5), 60, seed=1, horizon=100)
        assert requests and max(r.time for r in requests) < 100

    def test_requests_are_ordered_and_valid(self):
        requests = generate(MIXED, steady(0.4), 60, seed=9, count=100)
        assert [r.time for r in requests] == sorted(r.time for r in requests)
        assert all(1 <= r.source <= 60 and 1 <= r.dest <= 60 for r in requests)

    def test_exactly_one_bound_is_required(self):
        with pytest.raises(ValueError, match="exactly one"):
            generate(MIXED, steady(0.3), 60, seed=1)

    def test_a_dead_rate_is_an_error_not_a_hang(self):
        with pytest.raises(ValueError, match="rate zero"):
            generate(MIXED, steady(0.0), 60, seed=1, count=5, max_ticks=100)


def test_weights_shift_the_mixture():
    rng = Random(11)
    names = [pick(rng, [(0.9, LOBBY_UP), (0.1, TO_LOBBY)]).name for _ in range(500)]
    assert 0.8 < names.count("up") / len(names) < 1.0


def test_csv_round_trips_through_the_simulator_reader(tmp_path):
    requests = generate(MIXED, steady(0.3), 60, seed=1, count=30)
    path = tmp_path / "requests.csv"
    write_requests(path, requests)
    assert read_requests(path) == requests  # the engine's own reader


def test_a_count_met_on_the_last_allowed_tick_is_not_an_error():
    requests = generate(MIXED, bursts(5, 1), 60, seed=1, count=10, max_ticks=2)
    assert len(requests) == 10


def test_a_mixture_with_no_weight_is_an_error():
    with pytest.raises(ValueError, match="weight"):
        pick(Random(0), [(0.0, LOBBY_UP), (0.0, TO_LOBBY)])
