"""Command line for the evaluation harness (``elevator-trials``).

Two steps, in order: ``run`` works through the experiments (big-bang-0, big-bang-1 and the
stop-time pair) and ``report`` turns the results into
tables and figures. ``gen`` writes a single request file for inspection. Two follow-up
commands sit outside the reported results: ``sweep`` reruns a few presets at a range of
demands (demand = arrival rate as a fraction of the fleet's nominal throughput, see
``presets.rate_for``) and ``fairness`` sweeps the cost exponent at one cell.

Everything is read from and written to one results directory: ``docs/results`` under the
working directory, or wherever ``--results-dir`` points. The default is only used where it
already exists, so a command run away from the repository root stops with a message
instead of starting every experiment again in a new ``docs/`` tree.

Each subcommand is a ``cmd_*`` function taking the parsed arguments and returning the
process exit code. Exit codes: 0 success; 1 if ``run`` had failed cells, if ``main`` caught
an expected error (a missing file, a scheduler bug -- printed as one line), or if a preset
name is unknown; 2 for argparse usage errors. Any other exception still produces a
traceback.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from pathlib import Path

from .. import SchedulingError, SimulationStalled
from . import report, runner
from .presets import (
    BUILDINGS,
    BY_NAME,
    BY_PRESET_NAME,
    PRESETS,
    REFERENCE_BUILDING,
    Preset,
    rate_for,
    traffic_for,
)
from .traffic import write_requests

DEFAULT_RESULTS_DIR = Path("docs/results")
"""Results directory when ``--results-dir`` is not given, relative to the working directory."""


def results_dir(args: argparse.Namespace, *, writing: bool) -> Path:
    """Return the results directory a command works in.

    Args:
        args: Parsed arguments; uses ``results_dir`` (``None`` = not given).
        writing: Whether the command creates results. A command that only reads leaves a
            missing directory to its own "file not found" message.

    Returns:
        ``--results-dir`` if given (it is created as needed), else ``DEFAULT_RESULTS_DIR``.

    Raises:
        ValueError: ``--results-dir`` was not given, the command writes, and there is no
            ``docs/results`` under the working directory.
    """
    if args.results_dir is not None:
        return args.results_dir
    if writing and not DEFAULT_RESULTS_DIR.is_dir():
        raise ValueError(
            f"no {DEFAULT_RESULTS_DIR} under {Path.cwd()}; run from the repository root "
            f"or pass --results-dir"
        )
    return DEFAULT_RESULTS_DIR


def data_file(directory: Path, default: Path) -> Path:
    """Return where one of the harness's CSVs lives inside a results directory.

    Args:
        directory: The results directory.
        default: The file's default path, e.g. ``runner.RESULTS``; only its name is used.

    Returns:
        ``directory / "data" / <name>``.
    """
    return directory / "data" / default.name


def preset_named(name: str) -> Preset:
    """Return the preset with this name, or exit with a message listing the valid ones.

    Args:
        name: A preset name as typed on the command line, e.g. ``"morning-rush"``.

    Returns:
        The matching preset.

    Raises:
        SystemExit: no preset has that name.
    """
    try:
        return BY_PRESET_NAME[name]
    except KeyError:
        raise SystemExit(
            f"unknown preset {name!r}; try one of {[p.name for p in PRESETS]}"
        ) from None


def cmd_gen(args: argparse.Namespace) -> int:
    """Write the request file one preset produces on one building at one seed.

    It matches a file the experiments ran only when ``--seed`` is one of that preset's seeds
    (``Preset.seeds(building)``, e.g. 20000-20009 for morning-rush on B1); the default seed
    0 is not one of them. Either way it can be inspected or fed to ``elevator-sim``.

    Args:
        args: Parsed arguments; uses ``preset``, ``building``, ``seed`` and ``out``.

    Returns:
        0.

    Raises:
        SystemExit: the preset name is unknown.
    """
    preset, building = preset_named(args.preset), BY_NAME[args.building]
    rate = rate_for(preset, building)
    requests, _ = traffic_for(preset, building, rate, args.seed)
    write_requests(args.out, requests)
    print(f"{len(requests)} requests at {rate:.4f}/tick -> {args.out}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run every experiment (or the part chosen on the command line), resuming if interrupted.

    A cell with a results row but no series rows is re-run to backfill its series only.

    Warns first about existing rows generated at a different arrival rate, since those are
    skipped as already done rather than re-run. The warning covers every row in
    runs.csv, not only the presets and buildings selected.

    Warns too when the results were recorded under different source than today's
    (``runner.source_changed``): resume cannot tell, so the user has to.

    Args:
        args: Parsed arguments; uses ``preset`` and ``building`` (lists, empty = all) and
            ``jobs`` (``None`` = one worker per CPU).

    Returns:
        0 when every cell succeeded, 1 when any cell failed.

    Raises:
        SystemExit: a preset name is unknown.
        KeyError: runs.csv names a preset or building that no longer exists.
    """
    # An empty --preset / --building list means "all of them".
    presets = [preset_named(n) for n in args.preset] if args.preset else PRESETS
    buildings = [BY_NAME[n] for n in args.building] if args.building else BUILDINGS
    todo = list(runner.cells(presets, buildings))
    results = data_file(results_dir(args, writing=True), runner.RESULTS)
    series = results.parent / runner.SERIES.name
    print(f"{len(todo)} cells in scope; already-finished ones are skipped")
    print(f"results: {results.resolve()}")
    stale = runner.stale_cells(results)
    # Show only the first five stale rows; the count says how many there are.
    if stale:
        print(
            f"warning: {len(stale)} existing row(s) were generated at a different arrival "
            f"rate and will not be re-run; delete them to regenerate:"
        )
        for line in stale[:5]:
            print(f"  {line}")
    # Resume skips a finished cell whatever code produced it, so say when the code moved.
    if runner.source_changed(results):
        print(
            f"warning: the simulator or trial source has changed since {results} was "
            f"recorded (or it has no {runner.STAMP}); finished cells will not be re-run. "
            f"If the change can move results, delete {results} and "
            f"{series} and run again."
        )
    result = runner.run_all(todo, path=results, jobs=args.jobs)
    print(
        f"{result.written} new rows in {results}; "
        f"{result.series_backfilled} cell(s) had only their series backfilled"
    )
    # Any failure makes the exit code 1, so a script can tell the run is incomplete.
    if result.failures:
        print(f"{len(result.failures)} cell(s) failed and are missing from the results:")
        for failure in result.failures:
            print(f"  {failure}")
        return 1
    return 0


def cmd_fairness(args: argparse.Namespace) -> int:
    """Run the fairness-exponent sweep and print its summary table.

    The fairness exponent is the power ``p`` the ``eta-cost`` policy raises each rider's
    total journey time (request to drop-off) to; a larger ``p`` is meant to weigh long
    journeys more heavily.

    Args:
        args: Parsed arguments; this subcommand takes no options, so none are used.

    Returns:
        0.
    """
    path = data_file(results_dir(args, writing=True), runner.FAIRNESS)
    written = runner.run_fairness(path=path)
    print(f"{written} rows in {path}")
    for line in report.fairness_table(report.fairness_rows(path)):
        print(f"  {line}")
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """Run the demand sweep on the reference building, overwriting any previous sweep.

    Args:
        args: Parsed arguments; uses ``jobs`` (``None`` = one worker per CPU).

    Returns:
        0.
    """
    print(f"Sweeping demand over {runner.SWEEP_LOADS} on {', '.join(runner.SWEEP_PRESETS)}")
    path = data_file(results_dir(args, writing=True), runner.LOAD_SWEEP)
    written = runner.run_sweep(path=path, jobs=args.jobs)
    print(f"{written} rows in {path}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Write the markdown report and, unless ``--no-figures`` was given, the figures.

    Args:
        args: Parsed arguments; uses ``figures`` (``False`` when ``--no-figures`` was given).

    Returns:
        0.
    """
    directory = results_dir(args, writing=False)
    results = data_file(directory, runner.RESULTS)
    path = report.write_report(
        directory / report.REPORT.name, results, data_file(directory, runner.FAIRNESS)
    )
    print(f"Report: {path}")
    if args.figures:
        figures = directory / report.FIGURES.name
        made = report.write_figures(figures, results)
        print(f"Figures: {len(made)} in {figures}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the ``elevator-trials`` argument parser with one subcommand per step.

    Each subcommand stores its handler in ``func``, which ``main`` calls.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="elevator-trials",
        description="Generate traffic, run the scheduler comparison, and report it.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help=f"directory the results are read from and written to (default: "
        f"{DEFAULT_RESULTS_DIR} under the working directory, which must already exist "
        f"for a command that writes)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Shared --jobs option, attached to each subcommand that uses a worker pool.
    parallel = argparse.ArgumentParser(add_help=False)
    parallel.add_argument(
        "--jobs", type=int, default=None, help="worker processes (default: one per CPU)"
    )

    # gen defaults to seed 0 (not one of the experiments' seeds) on the reference building,
    # written to ./requests.csv.
    p = sub.add_parser("gen", help="write one preset's request file")
    p.add_argument("preset")
    p.add_argument(
        "--building", default=REFERENCE_BUILDING.name, choices=[b.name for b in BUILDINGS]
    )
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", type=Path, default=Path("requests.csv"))
    p.set_defaults(func=cmd_gen)

    # --preset and --building can each be repeated to pick several; none given = all.
    p = sub.add_parser(
        "run", parents=[parallel], help="run every experiment, resuming where it left off"
    )
    p.add_argument("--preset", action="append", default=[])
    p.add_argument("--building", action="append", default=[], choices=[b.name for b in BUILDINGS])
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("fairness", help="follow-up: sweep the eta-cost exponent at one cell")
    p.set_defaults(func=cmd_fairness)

    p = sub.add_parser(
        "sweep",
        parents=[parallel],
        help="follow-up: run the demand sweep on the reference building "
        "(always overwrites; unlike run, an interrupted sweep is not resumed)",
    )
    p.set_defaults(func=cmd_sweep)

    # Figures are on by default; --no-figures stores False into args.figures.
    p = sub.add_parser("report", help="write the markdown report and figures")
    p.add_argument("--no-figures", dest="figures", action="store_false")
    p.set_defaults(func=cmd_report)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and run the chosen subcommand.

    Args:
        argv: Arguments after the program name; ``None`` = read them from ``sys.argv``.

    Returns:
        The process exit code: the subcommand's own, or 1 if it raised an expected error.
        Argparse usage errors and unknown preset names exit via ``SystemExit`` instead.
    """
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        csv.Error,
        SchedulingError,
        SimulationStalled,
    ) as e:
        # Expected failures get a one-line message on stderr instead of a traceback.
        print(f"elevator-trials: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
