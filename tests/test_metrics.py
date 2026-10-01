"""The time-bucketed max."""

import pytest

from elevator_sim.models import Passenger, Request
from elevator_sim.trials.metrics import bucketed_max


def passenger(id, request_time, dropoff, source=1, dest=2):
    if dropoff is None:
        return Passenger(Request(request_time, id, source, dest), elevator_id=0)
    return Passenger(
        Request(request_time, id, source, dest),
        elevator_id=0,
        pickup_time=request_time,
        dropoff_time=dropoff,
    )


class TestBucketedMax:
    def test_buckets_on_request_time(self):
        people = [
            passenger("a", 0, 5),  # window 0: totals 5 and 30
            passenger("b", 9, 39),
            passenger("c", 10, 22),  # window 10: total 12
        ]
        assert bucketed_max(people, window=10) == [(0, 30), (10, 12)]

    def test_a_window_whose_arrivals_all_went_unserved_reports_none(self):
        people = [passenger("a", 0, 5), passenger("b", 10, None)]
        assert bucketed_max(people, window=10) == [(0, 5), (10, None)]

    def test_empty_windows_are_absent_rather_than_interpolated(self):
        assert bucketed_max([passenger("a", 0, 5), passenger("b", 25, 30)], 10) == [(0, 5), (20, 5)]

    def test_a_zero_window_is_an_error(self):
        with pytest.raises(ValueError, match="window"):
            bucketed_max([passenger("a", 0, 5)], window=0)
