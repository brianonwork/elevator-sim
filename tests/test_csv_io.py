from pathlib import Path

import pytest
from conftest import SAMPLE_REQUESTS as SAMPLE

from elevator_sim.csv_io import read_requests, write_passenger_log, write_positions_log
from elevator_sim.models import Passenger, Request


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "requests.csv"
    p.write_text(text)
    return p


def test_reads_sample_file():
    assert read_requests(SAMPLE) == [
        Request(0, "passenger1", 1, 51),
        Request(0, "passenger2", 1, 37),
        Request(10, "passenger3", 20, 1),
    ]


def test_accepts_columns_in_any_order_and_ignores_blank_lines(tmp_path):
    p = write(tmp_path, "id,dest,source,time\n\np,3,1,4\n")
    assert read_requests(p) == [Request(4, "p", 1, 3)]


def test_a_utf8_bom_header_is_accepted(tmp_path):
    # Excel's "CSV UTF-8" export starts the file with a byte-order mark.
    p = tmp_path / "requests.csv"
    p.write_text("time,id,source,dest\n0,p,1,3\n", encoding="utf-8-sig")
    assert read_requests(p) == [Request(0, "p", 1, 3)]


def test_spaces_around_header_names_are_ignored(tmp_path):
    p = write(tmp_path, "time, id , source, dest\n0, p, 1, 3\n")
    assert read_requests(p) == [Request(0, "p", 1, 3)]


def test_rejects_missing_column(tmp_path):
    p = write(tmp_path, "time,id,source\n0,p,1\n")
    with pytest.raises(ValueError, match="dest"):
        read_requests(p)


def test_rejects_non_integer_with_row_number(tmp_path):
    p = write(tmp_path, "time,id,source,dest\n0,p,one,3\n")
    with pytest.raises(ValueError, match="row 2"):
        read_requests(p)


def test_rejects_same_source_and_dest_with_row_number(tmp_path):
    p = write(tmp_path, "time,id,source,dest\n0,p,3,3\n")
    with pytest.raises(ValueError, match="row 2"):
        read_requests(p)


def test_empty_file_gives_no_requests(tmp_path):
    p = write(tmp_path, "time,id,source,dest\n")
    assert read_requests(p) == []


def test_write_positions_log(tmp_path):
    out = tmp_path / "positions.csv"
    write_positions_log(out, [(0, (1, 1)), (1, (2, 1))])
    assert out.read_text().splitlines() == ["time,elevator_0,elevator_1", "0,1,1", "1,2,1"]


def test_write_positions_log_with_no_ticks_writes_nothing(tmp_path):
    out = tmp_path / "positions.csv"
    write_positions_log(out, [])
    assert out.read_text() == ""


def test_write_passenger_log(tmp_path):
    done = Passenger(Request(2, "a", 1, 5), elevator_id=0, pickup_time=3, dropoff_time=7)
    riding = Passenger(Request(4, "b", 3, 1), elevator_id=1, pickup_time=6)
    out = tmp_path / "passengers.csv"
    write_passenger_log(out, [done, riding])
    assert out.read_text().splitlines() == [
        "id,request_time,source,dest,elevator,pickup_time,dropoff_time,wait_time,travel_time,total_time",
        "a,2,1,5,0,3,7,1,4,5",
        "b,4,3,1,1,6,,2,,",
    ]


@pytest.mark.parametrize(
    "row,match",
    [
        ("0,p1,1", "missing value"),          # short row: dest is absent
        ("0,p1,1,5,99", "extra field"),       # long row: a fifth value with no column
        ("0,p1,1,not-a-floor", "invalid literal"),
        ("0,   ,1,5", "missing value"),       # whitespace-only id: not None, but not an id
    ],
)
def test_a_malformed_row_raises_value_error_naming_the_row(tmp_path, row, match):
    path = tmp_path / "r.csv"
    path.write_text(f"time,id,source,dest\n{row}\n")
    with pytest.raises(ValueError, match=match) as exc:
        read_requests(path)
    assert "row 2" in str(exc.value)


def test_a_whitespace_only_id_names_the_column_rather_than_becoming_an_empty_id(tmp_path):
    # Whitespace survives `row.get("id") is None`, so without folding it into the absent
    # check it strips down to "" and only fails much later as a mystifying duplicate id.
    path = tmp_path / "r.csv"
    path.write_text("time,id,source,dest\n0,   ,1,5\n")
    with pytest.raises(ValueError, match=r"missing value.*\['id'\]") as exc:
        read_requests(path)
    assert "row 2" in str(exc.value)


def test_a_blank_line_is_skipped_even_with_trailing_commas(tmp_path):
    path = tmp_path / "r.csv"
    path.write_text("time,id,source,dest\n0,p1,1,5\n,,,\n\n")
    assert [r.id for r in read_requests(path)] == ["p1"]


def test_an_extra_column_is_allowed(tmp_path):
    # traffic.write_requests adds a flow column; only ragged rows are an error.
    path = tmp_path / "r.csv"
    path.write_text("time,id,source,dest,flow\n0,p1,1,5,up\n")
    assert [r.id for r in read_requests(path)] == ["p1"]
