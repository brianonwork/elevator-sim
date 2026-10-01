"""CSV input and output. Named csv_io to avoid shadowing the stdlib ``io`` module."""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from pathlib import Path

from .models import Passenger, Request

REQUEST_COLUMNS = ("time", "id", "source", "dest")
"""Columns every request CSV must have (in any order)."""
PASSENGER_COLUMNS = (
    "id",
    "request_time",
    "source",
    "dest",
    "elevator",
    "pickup_time",
    "dropoff_time",
    "wait_time",
    "travel_time",
    "total_time",
)
"""Header of the passenger log written by :func:`write_passenger_log`, in column order."""

_EXTRA = "__extra__"
"""``DictReader`` restkey. A row with more fields than its header lands here, which is the
only way to tell a ragged row from a file that simply carries extra columns."""

_INTEGER = re.compile(r"[+-]?[0-9]+")
"""What a time or floor cell may look like. Stricter than ``int()``, which also takes
``1_0`` and non-ASCII digits."""


def _integer(text: str) -> int:
    """Parse one CSV cell as a plain decimal integer.

    Args:
        text: The cell's text; surrounding whitespace is ignored.

    Returns:
        The integer value.

    Raises:
        ValueError: the cell is anything but an optional sign followed by ASCII digits.
    """
    text = text.strip()
    if not _INTEGER.fullmatch(text):
        raise ValueError(f"invalid integer: {text!r}")
    return int(text)


def read_requests(path: str | Path) -> list[Request]:
    """Parse a ``time,id,source,dest`` CSV. Columns may appear in any order.

    Extra columns are ignored, so a file may carry notes of its own (a ``flow`` label per
    row, say). Raises ``ValueError`` naming the offending row for anything else -- a row
    ragged against its own header, a missing value or an unparsable number -- so a bad file
    never reaches the caller as a ``TypeError``. Rows are returned in file order; the
    simulation sorts them by time itself, and checks ids are unique and floors fit the
    building (:func:`~elevator_sim.models.validate_requests`), which a file alone cannot.

    Args:
        path: CSV file to read.

    Returns:
        One :class:`Request` per non-blank row, in file order.

    Raises:
        ValueError: a required column is missing from the header or appears twice, or a
            row is ragged, has an empty value, or has a number that does not parse or is
            invalid (see :class:`Request`).
        OSError: the file cannot be opened.
    """
    # utf-8-sig drops the byte-order mark Excel writes, which would otherwise glue itself to
    # the first column name.
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, restkey=_EXTRA)
        # Tolerate "time, id, source, dest": values are stripped already, names should be too.
        if reader.fieldnames:
            reader.fieldnames = [name.strip() for name in reader.fieldnames]
        # Check the header first so a wrong file fails with one clear message.
        missing = [c for c in REQUEST_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: missing column(s) {missing}; need {list(REQUEST_COLUMNS)}")
        # DictReader keeps only the last of two same-named columns, silently.
        repeated = [c for c in REQUEST_COLUMNS if reader.fieldnames.count(c) > 1]
        if repeated:
            raise ValueError(f"{path}: column(s) {repeated} appear more than once in the header")

        requests: list[Request] = []
        for row in reader:
            line = reader.line_num
            # Skip a blank line, or one of nothing but separators.
            if not any((row.get(c) or "").strip() for c in REQUEST_COLUMNS):
                continue
            if row.get(_EXTRA):
                raise ValueError(
                    f"{path} row {line}: {len(row[_EXTRA])} extra field(s) with no column; "
                    f"header is {reader.fieldnames}"
                )
            # A row with some values but not all of them is an error, not a blank line.
            absent = [c for c in REQUEST_COLUMNS if not (row.get(c) or "").strip()]
            if absent:
                raise ValueError(f"{path} row {line}: missing value(s) for {absent}")
            try:
                request = Request(
                    time=_integer(row["time"]),
                    id=row["id"].strip(),
                    source=_integer(row["source"]),
                    dest=_integer(row["dest"]),
                )
            # Covers both unparsable numbers and Request's own checks; add the row number.
            except ValueError as e:
                raise ValueError(f"{path} row {line}: {e}") from None
            requests.append(request)
    return requests


def write_positions_log(path: str | Path, log: Iterable[tuple[int, tuple[int, ...]]]) -> None:
    """Write one row per tick: ``time,elevator_0,elevator_1,...``.

    The fleet size comes from the rows themselves rather than from a parameter that could
    disagree with them. An empty log writes no header, since there is nothing to describe.

    Args:
        path: CSV file to create or overwrite.
        log: ``(time, floors)`` pairs, floors ordered by elevator id.

    Raises:
        OSError: the file cannot be written.
    """
    rows = list(log)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        # Empty log: leave an empty file rather than guess a fleet size for the header.
        if not rows:
            return
        writer.writerow(["time", *(f"elevator_{i}" for i in range(len(rows[0][1])))])
        for time, floors in rows:
            writer.writerow([time, *floors])


def write_passenger_log(path: str | Path, passengers: Iterable[Passenger]) -> None:
    """Write one row per passenger with every timestamp and derived duration.

    Unknown values (a passenger never picked up or dropped off) are left blank.

    Args:
        path: CSV file to create or overwrite.
        passengers: Passengers to log, one row each, in the given order.

    Raises:
        OSError: the file cannot be written.
    """

    def cell(value: int | None) -> str:
        """Format a value for the CSV, turning ``None`` (not happened yet) into blank."""
        return "" if value is None else str(value)

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(PASSENGER_COLUMNS)
        # One row per passenger, in the order given (the CLI passes release order, i.e.
        # by request time); cell order matches PASSENGER_COLUMNS.
        for p in passengers:
            r = p.request
            writer.writerow(
                [
                    r.id,
                    r.time,
                    r.source,
                    r.dest,
                    cell(p.elevator_id),
                    cell(p.pickup_time),
                    cell(p.dropoff_time),
                    cell(p.wait_time),
                    cell(p.travel_time),
                    cell(p.total_time),
                ]
            )
