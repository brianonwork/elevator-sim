"""Running the experiments: one CSV row per cell, resumable, paired across schedulers.

This module runs the simulator many times and writes one results row per run. The vocabulary used
throughout:

- A **seed** is the number that fixes the random draw of a request file, so the same seed
  always produces the same passengers at the same ticks.
- A **cell** is one preset on one building in one variant under one scheduler at one seed:
  the smallest unit of work, one simulation run, one CSV row.
- The **experiments** are every cell there is (every preset x building x variant x scheduler
  x seed); ``run`` works through them. The variant picks the experiment: ``plain`` cells are
  big-bang-0 (free stops), ``stop1`` cells are big-bang-1 (1-tick stops), and B1's ``stop3``
  cells for preset 8 are the stop-time pair's 3-tick half.
- **Demand** (a preset's ``load``) is the arrival rate as a fraction of the fleet's
  **nominal throughput**, ``cars * capacity / (2 * floors)`` riders per tick (see
  ``presets.rate_for``). Every standard preset runs at 0.75 of it and overload at 1.5.
- A **sweep** varies one knob over a list of values with everything else held fixed:
  ``run_sweep`` varies demand, ``run_fairness`` varies the **fairness exponent** (the power
  ``p`` the ``eta-cost`` policy raises each rider's total journey time to, request to
  drop-off; a larger ``p`` is meant to punish long journeys harder, trading mean for worst
  case -- at the recorded fairness cell it made no measurable difference, see
  ``docs/write-up/5-findings.md``).
- A run is **resumable** when rerunning it skips cells already in the results file instead
  of starting over, so an interrupted run can pick up where it stopped.

Every scheduler sees the identical request file for a given seed, which is what pairs the
comparison: the draw cancels out and only assignment differs. That is a **paired
comparison** -- scheduler A and scheduler B are compared seed by seed on the same traffic.
"""

from __future__ import annotations

import csv
import hashlib
import os
import time
from collections.abc import Iterator, Sequence
from dataclasses import asdict, astuple, dataclass
from multiprocessing import Pool
from pathlib import Path

from ..algorithms import BUILTINS, create
from ..simulation import Simulation
from ..stats import summarize
from .metrics import bucketed_max
from .presets import (
    BUILDINGS,
    BY_NAME,
    BY_NUMBER,
    BY_PRESET_NAME,
    PLAIN,
    PRESETS,
    REFERENCE_BUILDING,
    Building,
    Preset,
    Variant,
    config_for,
    rate_for,
    traffic_for,
)

RESULTS = Path("docs/results/data/runs.csv")
"""Every experiment's output: one row per cell, appended to as cells finish."""
SERIES = Path("docs/results/data/runs-windows.csv")
"""Per-window max total time for every cell, one row per window."""
SCHEDULERS = tuple(BUILTINS)
"""Every built-in scheduler name, in ``BUILTINS`` order; the tables use this column order."""
MAX_TICKS = 200_000
"""Runaway guard. Every run drains (overload stops *arrivals* at its horizon, then runs until
everyone is delivered), so this only catches bugs."""
SERIES_WINDOW = 150
"""Width in ticks of one window in the per-window series (by request time)."""
SERIES_COLUMNS = ("preset", "building", "variant", "scheduler", "seed",
                  "window_start", "total_max")
"""Header of ``runs-windows.csv``: the cell's identity, then one window's start tick and max."""

STAMP = "runs.source-hash"
"""File beside ``runs.csv`` holding the fingerprint of the code that produced its rows."""
UNHASHED = ("cli.py", "trials/cli.py", "trials/report.py")
"""Source files left out of the fingerprint: they parse arguments and draw tables, and
cannot change a results row."""

COLUMNS = (
    "preset", "building", "variant", "scheduler", "seed",
    "served", "unserved",
    "wait_min", "wait_max", "wait_mean", "wait_p95",
    "total_min", "total_max", "total_mean", "total_p95",
    "span", "seconds",
)
"""Header of ``runs.csv``. Times are in ticks; ``seconds`` is the CPU time the simulation took
(``time.process_time`` in the worker that ran it). Every run drains, so ``unserved`` is always
0; it is kept as a check."""


@dataclass(frozen=True)
class Cell:
    """The identity of one simulation run: which traffic, building, setup, scheduler, seed."""

    preset: str
    """Preset name, e.g. ``"morning-rush"``."""
    building: str
    """Building name, e.g. ``"B1"``."""
    variant: str
    """Variant name, e.g. ``"plain"`` or ``"stop3"``."""
    scheduler: str
    """Scheduler name, a key of ``BUILTINS``."""
    seed: int
    """Seed of the request file; every scheduler run at this preset, building, variant and
    seed shares it, which is what pairs the comparison."""


@dataclass(frozen=True)
class RunResult:
    """What one ``run_all`` call achieved, so a partial run is not mistaken for a whole one."""

    written: int
    """Rows appended to the results file by this call."""
    series_backfilled: int
    """Cells that already had a results row but no series rows, and got only their series
    rows appended by this call."""
    failures: list[str]
    """One line per cell whose worker raised, in completion order."""


def cells(
    presets: Sequence[Preset] = PRESETS,
    buildings: Sequence[Building] = BUILDINGS,
    schedulers: Sequence[str] = SCHEDULERS,
) -> Iterator[Cell]:
    """Yield every cell of the experiments, in a stable order.

    Args:
        presets: Presets to include; defaults to all of them.
        buildings: Buildings to include; defaults to all of them.
        schedulers: Scheduler names to include; defaults to every built-in.

    Yields:
        One ``Cell`` per preset x building x variant x scheduler x seed, with the seed
        varying fastest.
    """
    for preset in presets:
        for building in buildings:
            for variant in preset.variants(building):
                for scheduler in schedulers:
                    for seed in preset.seeds(building):
                        yield Cell(preset.name, building.name, variant.name, scheduler, seed)


def variant_of(preset: Preset, building: Building, name: str) -> Variant:
    """Return the variant of this preset on this building that has the given name.

    Args:
        preset: The preset whose variants are searched.
        building: The building the variants are generated for.
        name: The variant name stored in a ``Cell``.

    Returns:
        The matching ``Variant``.

    Raises:
        KeyError: no variant of that name exists for this preset and building.
    """
    by_name = {v.name: v for v in preset.variants(building)}
    if name not in by_name:
        raise KeyError(
            f"no variant {name!r} for {preset.name} on {building.name}; "
            f"it has {sorted(by_name)}"
        )
    return by_name[name]


def run_cell(cell: Cell, *, load: float | None = None) -> dict[str, object]:
    """Simulate one cell and reduce it to a results row.

    ``load`` overrides the preset's own demand (as a fraction of the building's nominal
    throughput), which is what the sweep varies. Everything else about the cell is unchanged.

    Args:
        cell: The run to perform.
        load: Demand to run at; ``None`` = the preset's own.

    Returns:
        A dict with one entry per ``COLUMNS`` name, plus a private ``"_series"`` list of
        ``runs-windows.csv`` rows, one per window that had arrivals. Times are in ticks.

    Raises:
        KeyError: the cell names an unknown preset, building, variant or scheduler.
    """
    # Turn the cell's names back into the objects they stand for.
    preset = BY_PRESET_NAME[cell.preset]
    building = BY_NAME[cell.building]
    variant = variant_of(preset, building, cell.variant)
    # Arrival rate in requests/tick: the preset's own demand, or the sweep's override.
    rate = rate_for(preset, building) if load is None else load * building.nominal_rate

    requests, span = traffic_for(preset, building, rate, cell.seed)

    # Run the simulation to the end, timing it so the report can show each scheduler's
    # compute cost. Overload's arrivals stop at its horizon; the run still drains, so every
    # passenger is scored.
    config = config_for(building, variant)
    scheduler = create(cell.scheduler, config)
    # CPU time, not wall-clock: cells run in a pool with one worker per CPU, where the wall
    # clock also counts the time a worker spends waiting for a core (measured at about 2.5
    # times the serial figure, and up to 7 times on single cells).
    started = time.process_time()
    result = Simulation(config, scheduler, requests).run(max_ticks=MAX_TICKS)
    elapsed = time.process_time() - started

    # Summarise every passenger: nothing is trimmed from the start of the run.
    summary = summarize(result.passengers)
    series = bucketed_max(result.passengers, SERIES_WINDOW)

    # Means are rounded to keep the CSV readable; the "_series" key rides along for the
    # parent process to write to runs-windows.csv, and DictWriter ignores it for runs.csv.
    return {
        "_series": [
            {**asdict(cell), "window_start": start, "total_max": value}
            for start, value in series if value is not None
        ],
        **asdict(cell),
        "served": summary.count, "unserved": summary.unserved,
        "wait_min": summary.wait.min, "wait_max": summary.wait.max,
        "wait_mean": round(summary.wait.mean, 3), "wait_p95": summary.wait.p95,
        "total_min": summary.total.min, "total_max": summary.total.max,
        "total_mean": round(summary.total.mean, 3), "total_p95": summary.total.p95,
        "span": span,
        "seconds": round(elapsed, 3),
    }


def done_cells(path: Path = RESULTS) -> set[tuple]:
    """Return the cells already in the results file, so a run can be resumed.

    Args:
        path: The results CSV to read.

    Returns:
        ``(preset, building, variant, scheduler, seed)`` tuples, the same shape as
        ``astuple(Cell)``; empty when the file does not exist yet.
    """
    if not path.exists():
        return set()
    with open(path, newline="") as f:
        return {
            (r["preset"], r["building"], r["variant"], r["scheduler"], int(r["seed"]))
            for r in csv.DictReader(f)
        }


def done_series(path: Path = SERIES) -> set[tuple]:
    """Return the cells that already have rows in the series file.

    Every cell scores hundreds of passengers, so it always has at least one window; a cell
    with no series rows has simply not been recorded yet (for example, a results row written
    before every preset recorded a series).

    Args:
        path: The series CSV to read.

    Returns:
        ``(preset, building, variant, scheduler, seed)`` tuples, the same shape as
        ``astuple(Cell)``; empty when the file does not exist or is empty.
    """
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with open(path, newline="") as f:
        return {
            (r["preset"], r["building"], r["variant"], r["scheduler"], int(r["seed"]))
            for r in csv.DictReader(f)
        }


def stale_cells(path: Path = RESULTS) -> list[str]:
    """Return rows whose recorded ``span`` no longer matches the current arrival rate.

    In short: if the demand rule or a building's configuration changed after some rows were
    written, those rows were run at a different arrival rate, and this finds them so
    ``cmd_run`` can warn.

    There is no ``rate`` column -- adding one would change ``COLUMNS`` and require rewriting
    every row already in ``runs.csv``, which ``run_all`` only ever appends to. ``span``
    is already recorded on every row, and for a request-bounded preset it is a function of
    the rate alone (``max(1, round(building.requests / rate))``, ``Preset.span``), so a
    mismatch there is evidence the row was generated at a different rate. ``done_cells`` still
    keys on cell identity alone, so resuming after a rate change would otherwise mix rates
    silently; this is how ``cmd_run`` warns about that instead.

    ``overload``'s arrivals are tick-bounded: its span is always ``horizon`` (1500) regardless
    of rate, so staleness is not detectable for it from ``span`` alone -- its rows are skipped
    rather than guessed at. An honest gap beats a wrong check.

    Args:
        path: The results CSV to check.

    Returns:
        One ``preset/building/scheduler/seed`` label per stale row; empty when the file does
        not exist or nothing is stale.

    Raises:
        KeyError: a row names a preset or building that no longer exists.
    """
    if not path.exists():
        return []
    stale = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            preset, building = BY_PRESET_NAME[r["preset"]], BY_NAME[r["building"]]
            # Tick-bounded preset: span is the horizon whatever the rate, so it proves nothing.
            if preset.horizon:
                continue
            if int(r["span"]) != preset.span(building, rate_for(preset, building)):
                stale.append(f"{r['preset']}/{r['building']}/{r['scheduler']}/{r['seed']}")
    return stale


def fingerprinted_files() -> list[Path]:
    """Return the source files whose contents decide a results row.

    Returns:
        Every ``.py`` file of the package except ``UNHASHED``, as sorted paths relative to
        the package root.
    """
    root = Path(__file__).resolve().parent.parent
    files = (p.relative_to(root) for p in root.rglob("*.py"))
    return sorted(p for p in files if p.as_posix() not in UNHASHED)


def source_fingerprint() -> str:
    """Return a hash of the simulator, scheduler and trial-design source.

    ``done_cells`` keys on a cell's identity alone, so after an edit to the controller, a
    policy or a preset every old row still reads as finished and ``run`` would write
    nothing. Nothing in a row says which code produced it; this does, for the whole file.
    It is deliberately coarse: a comment edit changes it too, and the cost of that is one
    warning, where the cost of missing a real change is a report built on stale numbers.

    Returns:
        A SHA-256 hex digest over each file's relative path and bytes.
    """
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    for relative in fingerprinted_files():
        digest.update(relative.as_posix().encode())
        digest.update((root / relative).read_bytes())
    return digest.hexdigest()


def stamp_path(path: Path = RESULTS) -> Path:
    """Return where the fingerprint for a results file is kept.

    Args:
        path: The results CSV.

    Returns:
        ``STAMP`` in the same directory.
    """
    return path.parent / STAMP


def source_changed(path: Path = RESULTS) -> bool:
    """Return whether existing results were recorded under different code than today's.

    Args:
        path: The results CSV to check.

    Returns:
        True when ``path`` holds rows and its stamp is missing or differs from
        :func:`source_fingerprint`; False when there are no results yet or the stamp matches.
    """
    if not path.exists() or path.stat().st_size == 0:
        return False
    stamp = stamp_path(path)
    return not stamp.exists() or stamp.read_text().strip() != source_fingerprint()


def _work(payload):
    """Run one cell in a pool worker, for both the experiments and the sweep.

    Args:
        payload: A ``(cell, load)`` tuple, packed because ``Pool.imap`` passes one
            argument. ``load`` is ``None`` off the sweep (use the preset's own demand).

    Returns:
        The ``run_cell`` row, with a ``"load"`` key added on the sweep. If the cell raised,
        instead a row holding the cell's identity, ``"load"`` if any, and an ``"error"``
        message.
    """
    cell, load = payload
    at_load = {} if load is None else {"load": load}
    try:
        return {**at_load, **run_cell(cell, load=load)}
    # One bad cell should not lose the rest of the run, so report it instead of raising.
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", **at_load, **asdict(cell)}


def run_all(
    todo: Sequence[Cell] | None = None,
    *,
    path: Path = RESULTS,
    jobs: int | None = None,
) -> RunResult:
    """Run every outstanding cell, appending rows as they finish.

    A cell is finished only when it has both its row in ``path`` and its rows in
    ``runs-windows.csv`` (in the same directory as ``path``). A cell missing either is run and
    only the missing part is written, which is what makes a run resumable and lets a
    results file written before every preset recorded a series be backfilled without
    touching its existing rows. A rerun reproduces every column except ``seconds``, so
    keeping the old results row loses nothing.

    Args:
        todo: Cells to consider; ``None`` = every cell of every experiment.
        path: The results CSV to append to (created, with its header, if missing).
        jobs: Worker processes; ``None`` = one per CPU.

    Returns:
        The results rows written, the cells whose series alone were backfilled, and any
        cells that failed.
    """
    series_path = path.parent / SERIES.name
    # Resume: a cell is done only once both files hold it.
    have_results = done_cells(path)
    have_series = done_series(series_path)
    pending = [c for c in (todo if todo is not None else cells())
               if astuple(c) not in have_results or astuple(c) not in have_series]
    if not pending:
        return RunResult(0, 0, [])

    path.parent.mkdir(parents=True, exist_ok=True)
    # A zero-byte file exists but has no header, and appending to it would promote the first
    # data row to one -- which breaks done_cells and every reader downstream.
    fresh = not path.exists() or path.stat().st_size == 0
    fresh_series = not series_path.exists() or series_path.stat().st_size == 0
    # Stamp only a run that starts from nothing: every row it will hold comes from this
    # code. Rows already on disk keep whatever stamp they were recorded under.
    if fresh and fresh_series:
        stamp_path(path).write_text(source_fingerprint() + "\n")
    written = backfilled = 0
    # The parent owns both files: workers return their rows rather than appending, so
    # there is no interleaving between processes.
    with open(path, "a", newline="") as f, open(series_path, "a", newline="") as sf:
        # extrasaction="ignore" lets the row's private "_series" key through without error.
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        series_writer = csv.DictWriter(sf, fieldnames=SERIES_COLUMNS)
        if fresh:
            writer.writeheader()
        if fresh_series:
            series_writer.writeheader()
        payloads = [(c, None) for c in pending]
        failures: list[str] = []
        # Unordered: rows are written as soon as any worker finishes, not in cell order.
        with Pool(jobs or os.cpu_count()) as pool:
            for row in pool.imap_unordered(_work, payloads):
                # A failed cell is reported and skipped, so it is retried on the next run.
                if "error" in row:
                    failure = (f"{row['preset']}/{row['building']}/{row['variant']}"
                               f"/{row['scheduler']}/{row['seed']}: {row['error']}")
                    print(f"  !! {failure}")
                    failures.append(failure)
                    continue
                cell_key = (row["preset"], row["building"], row["variant"], row["scheduler"],
                            row["seed"])
                series_rows = row.pop("_series", [])
                # Write only the part this cell is missing, so neither file gets duplicates.
                if cell_key not in have_series:
                    series_writer.writerows(series_rows)
                if cell_key not in have_results:
                    writer.writerow(row)
                    written += 1
                else:
                    backfilled += 1
                # Flush every row so an interrupted run keeps everything finished so far.
                f.flush()
                sf.flush()
                # Progress line every 100 cells.
                done = written + backfilled
                if done % 100 == 0:
                    print(f"  {done}/{len(pending)} cells")
    return RunResult(written, backfilled, failures)


LOAD_SWEEP = Path("docs/results/data/load_sweep.csv")
"""The demand sweep's output, rewritten from scratch on every ``run_sweep`` call. A
follow-up, not part of the reported experiments (see ``docs/write-up/6-follow-ups.md``)."""
SWEEP_LOADS = (0.4, 0.6, 0.8, 0.9, 1.0, 1.2)
"""Demands as a fraction of the fleet's nominal throughput, either side of the standard 0.75."""
SWEEP_PRESETS = ("baseline", "morning-rush", "overload")
"""One uniform, one directional, one saturating. B1 only: the point is the curve, not the cells."""
SWEEP_COLUMNS = ("load", *COLUMNS)
"""Header of ``load_sweep.csv``: the load, then the same columns as ``runs.csv``."""


def run_sweep(*, path: Path = LOAD_SWEEP, jobs: int | None = None) -> int:
    """Run every scheduler at every demand on the sweep presets.

    The experiments report one demand per preset, which cannot say whether a gap is a property of
    the scheduler or of the operating point it was measured at. This is the same comparison
    as a function of demand.

    Always writes fresh: unlike ``run_all``, which resumes an interrupted run by skipping
    cells already in ``path``, this truncates ``path`` on every call. An interrupted sweep
    must be rerun in full.

    Args:
        path: The sweep CSV to overwrite.
        jobs: Worker processes; ``None`` = one per CPU.

    Returns:
        The number of rows written. Failed cells are printed and not counted.
    """
    # Every (cell, load) pair on the reference building, for the sweep presets only, with
    # free stops: the sweep extends big-bang-0, so its other variants are left out.
    payloads = [
        (cell, load)
        for name in SWEEP_PRESETS
        for cell in cells([BY_PRESET_NAME[name]], [REFERENCE_BUILDING])
        if cell.variant == PLAIN.name
        for load in SWEEP_LOADS
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    # "w", not "a": the sweep is never resumed, so start from an empty file.
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SWEEP_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        # Ordered imap keeps the rows in payload order, so the file reads as a curve.
        with Pool(jobs or os.cpu_count()) as pool:
            for row in pool.imap(_work, payloads):
                if "error" in row:
                    print(f"  !! {row['preset']} @ {row['load']}: {row['error']}")
                    continue
                writer.writerow(row)
                written += 1
    return written


FAIRNESS = Path("docs/results/data/exponent_sweep.csv")
"""The fairness-exponent sweep's output, rewritten on every ``run_fairness`` call. A
follow-up, not part of the reported experiments (see ``docs/write-up/6-follow-ups.md``)."""
POWERS = (1.0, 1.5, 2.0, 3.0)
"""Fairness exponents tried: 1.0 is ``eta-cost``, 2.0 is ``eta-cost-fair``, plus 1.5 and 3."""
FAIRNESS_PRESET = 4
"""Skewed two-way: 85% up from the lobby, 15% down to it, the cell pre-registered for the
fairness question."""
FAIRNESS_SEEDS = 5
"""Seeds the exponent sweep runs, the first five of B1's ten. The report says so, so the
sweep's precision is not mistaken for the experiments'."""
FAIRNESS_COLUMNS = ("power", "seed", "total_mean", "total_p95", "total_max")
"""One row per (power, seed). Storing only the across-seed mean made the pre-registered test
impossible to recompute from its own output, which is why the write-up had to fall back to
pooling big-bang-0 runs."""


def run_fairness(*, path: Path = FAIRNESS, seeds: int = FAIRNESS_SEEDS) -> int:
    """Sweep the cost exponent with the environment held at one cell.

    A treatment knob has to be varied on its own, or the effect of the knob and the effect of
    the cell cannot be told apart. ``BUILTINS`` holds only p=1 and p=2, so the scheduler is
    built directly. One row per seed, so the comparison stays paired.

    Runs serially (no worker pool) and overwrites ``path`` on every call.

    Args:
        path: The fairness CSV to overwrite.
        seeds: How many of the preset's B1 seeds to run, taken from the front of the list.

    Returns:
        The number of rows written: one per exponent per seed.
    """
    # Imported here rather than at the top: only this function builds schedulers directly.
    from ..algorithms import DestinationDispatch, EtaCost

    # The fixed environment: skewed two-way traffic on B1, plain variant, at its own demand.
    preset, building = BY_NUMBER[FAIRNESS_PRESET], REFERENCE_BUILDING
    rate = rate_for(preset, building)
    config = config_for(building, PLAIN)
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FAIRNESS_COLUMNS)
        writer.writeheader()
        # Only the exponent changes between rows at the same seed: same traffic, same fleet.
        for power in POWERS:
            for seed in preset.seeds(building)[:seeds]:
                requests, _ = traffic_for(preset, building, rate, seed)
                result = Simulation(
                    config, DestinationDispatch(EtaCost(power=power)), requests
                ).run(max_ticks=MAX_TICKS)
                summary = summarize(result.passengers)
                writer.writerow({
                    "power": power, "seed": seed,
                    "total_mean": round(summary.total.mean, 3),
                    "total_p95": summary.total.p95,
                    "total_max": summary.total.max,
                })
                written += 1
    return written
