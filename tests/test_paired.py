"""Paired statistics over common random numbers."""

import pytest

from elevator_sim.trials.paired import paired_diff


def test_a_consistent_winner_is_separated():
    a = {0: 10.0, 1: 12.0, 2: 11.0}
    b = {0: 20.0, 1: 22.0, 2: 21.0}
    d = paired_diff(a, b)
    assert d.n == 3
    assert d.mean == pytest.approx(10.0)
    assert d.separated()


def test_identical_arms_are_not_separated():
    a = {0: 10.0, 1: 12.0}
    d = paired_diff(a, dict(a))
    assert d.mean == 0.0 and d.se == 0.0
    assert not d.separated()


def test_an_identical_gap_on_every_seed_is_separated():
    # Zero spread and a positive mean: the paired t statistic is +inf, so the difference
    # separates at any k. Reachable with integer metrics at three seeds -- `total_max`, say,
    # where one arm beats the other by exactly the same count on every seed. Pinned
    # because `separated` short-circuits on `mean > 0` rather than dividing by `se`.
    a = {0: 10.0, 1: 12.0, 2: 11.0}
    b = {0: 13.0, 1: 15.0, 2: 14.0}
    d = paired_diff(a, b)
    assert d.n == 3
    assert d.mean == pytest.approx(3.0) and d.se == 0.0
    assert d.separated()
    assert d.separated(k=1e9)


def test_noise_larger_than_the_gap_is_not_separated():
    a = {0: 10.0, 1: 10.0, 2: 10.0}
    b = {0: 40.0, 1: 0.0, 2: 1.0}  # mean +3.7, swamped by its own spread
    assert not paired_diff(a, b).separated()


def test_a_single_shared_seed_has_no_standard_error():
    with pytest.raises(ValueError, match="two"):
        paired_diff({0: 1.0}, {0: 2.0})


def test_a_three_seed_lead_past_two_se_but_short_of_the_t_critical_is_not_separated():
    # mean 3.0, se ~1.1547: clears a fixed k=2.0 (ratio 2.60) but not the df=2 t-critical
    # of 4.303, which a three-seed comparison must actually clear.
    a = {0: 0.0, 1: 0.0, 2: 0.0}
    b = {0: 5.0, 1: 3.0, 2: 1.0}
    d = paired_diff(a, b)
    assert d.n == 3
    assert d.mean == pytest.approx(3.0)
    assert d.mean > 2.0 * d.se
    assert not d.separated()
    assert d.separated(k=2.0)
