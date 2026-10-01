import pytest

from elevator_sim.models import Passenger, Request
from elevator_sim.stats import (
    Distribution,
    FleetSummary,
    Summary,
    distribution,
    fleet_summary,
    format_fleet,
    format_summary,
    percentile,
    summarize,
)


def done(id, request_time, pickup, dropoff):
    return Passenger(
        Request(request_time, id, 1, 2), elevator_id=0, pickup_time=pickup, dropoff_time=dropoff
    )


def test_summarize_min_max_mean_and_p95():
    passengers = [
        done("a", 0, 0, 4),   # wait 0, travel 4, total 4
        done("b", 0, 2, 10),  # wait 2, travel 8, total 10
        done("c", 5, 12, 13), # wait 7, travel 1, total 8
    ]
    s = summarize(passengers)
    assert (s.count, s.unserved) == (3, 0)
    assert (s.wait.min, s.wait.max, s.wait.mean, s.wait.p95) == (0, 7, 3.0, 7)
    assert (s.travel.min, s.travel.max, s.travel.p95) == (1, 8, 8)
    assert s.travel.mean == pytest.approx(13 / 3)
    assert (s.total.min, s.total.max, s.total.p95) == (4, 10, 10)
    assert s.total.mean == pytest.approx(22 / 3)


def test_distribution_gives_the_five_number_summary_mean_and_sd():
    d = distribution([10, 1, 9, 2, 8, 3, 7, 4, 6, 5])
    # Nearest rank on 1..10: q1 is the 3rd value, the median the 5th, q3 the 8th.
    assert (d.min, d.q1, d.median, d.q3, d.p95, d.max) == (1, 3, 5, 8, 10, 10)
    assert d.iqr == 5
    assert d.mean == 5.5
    # Population sd of 1..10 is sqrt(8.25).
    assert d.sd == pytest.approx(8.25 ** 0.5)


def test_one_value_has_no_spread():
    d = distribution([7])
    assert (d.min, d.q1, d.median, d.q3, d.max, d.mean, d.sd) == (7, 7, 7, 7, 7, 7, 0)


def test_distribution_of_nothing_raises():
    with pytest.raises(ValueError, match="non-empty"):
        distribution([])


def test_summarize_counts_unfinished_passengers_separately():
    unfinished = Passenger(Request(0, "x", 1, 2), elevator_id=0, pickup_time=1)
    s = summarize([done("a", 0, 1, 3), unfinished])
    assert (s.count, s.unserved) == (1, 1)


def test_summarize_empty_raises():
    with pytest.raises(ValueError):
        summarize([])


@pytest.mark.parametrize("q,expected", [(25, 1), (50, 3), (95, 10), (100, 10)])
def test_percentile_is_nearest_rank(q, expected):
    assert percentile([10, 1, 5, 3], q) == expected


def test_percentile_rejects_an_empty_sequence():
    with pytest.raises(ValueError, match="non-empty"):
        percentile([], 95)


def _summary(unserved):
    wait = Distribution(0, 1, 2, 3, 4, 4, 2.0, 1.5)
    travel = Distribution(1, 2, 4, 5, 5, 5, 4.0, 1.25)
    total = Distribution(3, 4, 6, 8, 9, 9, 6.0, 2.0)
    return Summary(2, unserved, wait, travel, total)


def test_format_summary_is_a_table_with_one_row_per_time():
    text = format_summary(_summary(0))
    assert "outstanding" not in text.lower()
    lines = text.splitlines()
    assert lines[1].split() == ["min", "q1", "median", "q3", "p95", "max", "mean", "sd"]
    assert lines[3].startswith("Travel time ")
    assert lines[3].split()[2:] == ["1", "2", "4", "5", "5", "5", "4.00", "1.25"]
    # Every row is the same width, so the columns line up under the header.
    assert len({len(line) for line in lines[1:]}) == 1


def test_format_summary_reports_outstanding_only_when_there_are_some():
    assert "Still outstanding: 3" in format_summary(_summary(3))


def test_percentile_rank_is_exact_where_float_arithmetic_is_not():
    # 0.07 * 100 is 7.000000000000001 in floating point, which rounded the rank up to 8.
    assert percentile(list(range(1, 101)), 7) == 7


def test_fleet_summary_counts_riders_moving_ticks_and_the_peak_queue():
    passengers = [
        Passenger(Request(0, "a", 1, 3), elevator_id=0, pickup_time=0, dropoff_time=2),
        Passenger(Request(0, "b", 3, 1), elevator_id=1, pickup_time=2, dropoff_time=4),
        Passenger(Request(1, "c", 2, 3), elevator_id=0, pickup_time=3, dropoff_time=4),
    ]
    log = [(0, (1, 1)), (1, (2, 2)), (2, (3, 3)), (3, (2, 2)), (4, (3, 1))]
    f = fleet_summary(passengers, log)
    assert f.riders == (2, 1)
    assert f.moving == (4, 4)
    assert f.ticks == 4
    # b waits from 0 to 2 and c from 1 to 3: two waiting at ticks 1 and 2.
    assert (f.peak_waiting, f.peak_waiting_time) == (2, 1)


def test_fleet_summary_ignores_a_same_tick_pickup_and_counts_the_unpicked():
    passengers = [
        Passenger(Request(0, "a", 1, 2), elevator_id=0, pickup_time=0, dropoff_time=1),
        Passenger(Request(1, "b", 2, 1)),
    ]
    f = fleet_summary(passengers, [(0, (1,)), (1, (2,))])
    assert f.riders == (1,)
    assert (f.peak_waiting, f.peak_waiting_time) == (1, 1)


def test_fleet_summary_of_an_empty_log_raises():
    with pytest.raises(ValueError):
        fleet_summary([], [])


def test_format_fleet_has_one_row_per_car_and_the_peak():
    f = FleetSummary(riders=(3, 0), moving=(5, 0), ticks=10, peak_waiting=2, peak_waiting_time=7)
    lines = format_fleet(f).splitlines()
    assert lines[1].split() == ["elevator_0", "3", "50%"]
    assert lines[2].split() == ["elevator_1", "0", "0%"]
    assert lines[3] == "Peak waiting: 2 passenger(s) at tick 7"


def test_format_fleet_survives_a_one_tick_run():
    f = FleetSummary(riders=(0,), moving=(0,), ticks=0, peak_waiting=0, peak_waiting_time=0)
    assert format_fleet(f).splitlines()[1].split() == ["elevator_0", "0", "0%"]
