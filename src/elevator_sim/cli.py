"""Command-line entry point (``elevator-sim``). Argument parsing and wiring only."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .algorithms import DEFAULT_NAME, available, create
from .csv_io import read_requests, write_passenger_log, write_positions_log
from .models import BuildingConfig
from .scheduler import SchedulingError
from .simulation import Simulation, SimulationStalled
from .stats import fleet_summary, format_fleet, format_summary, summarize


def build_parser(schedulers: list[str], default: str) -> argparse.ArgumentParser:
    """Build the command-line parser for ``elevator-sim``.

    Args:
        schedulers: Names allowed for ``--scheduler``.
        default: Scheduler used when ``--scheduler`` is not given.

    Returns:
        A parser ready for ``parse_args``.
    """
    parser = argparse.ArgumentParser(
        prog="elevator-sim",
        description="Discrete-time elevator simulation with pluggable schedulers.",
    )
    # Building and fleet shape; these become the BuildingConfig.
    parser.add_argument("requests", help="CSV of requests with columns time,id,source,dest")
    parser.add_argument("--elevators", type=int, default=1, help="number of cars (default 1)")
    parser.add_argument("--floors", type=int, default=10, help="floors, numbered 1..N (default 10)")
    parser.add_argument("--capacity", type=int, default=8, help="max riders per car (default 8)")
    parser.add_argument(
        "--stop-time", type=int, default=0,
        help="ticks a car is held after anyone boards or alights (default 0: stops are free)",
    )
    parser.add_argument(
        "--scheduler",
        choices=schedulers,
        default=default,
        help=f"scheduling algorithm to use (default {default})",
    )
    # Where the two output logs are written.
    parser.add_argument(
        "--positions-out", default="positions.csv", help="per-tick elevator positions log"
    )
    parser.add_argument(
        "--passengers-out", default="passengers.csv", help="per-passenger timing log"
    )
    # 100,000 ticks is far longer than any sensible input needs; it mainly catches a
    # scheduler that never delivers someone, so the run cannot loop forever. It also aborts
    # any input whose requests (or deliveries) extend past this tick.
    parser.add_argument(
        "--max-ticks", type=int, default=100_000,
        help="abort if the run exceeds this (default 100000)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one simulation from the command line and write its logs.

    Args:
        argv: Command-line arguments without the program name; ``None`` = ``sys.argv``.

    Returns:
        Process exit code: 0 on success, 1 if reading the input, building the config,
        the run, or writing the output logs failed (the error is printed to stderr).
        Invalid arguments exit with status 2 through argparse.
    """
    args = build_parser(available(), DEFAULT_NAME).parse_args(argv)

    # Any expected failure (bad file, bad config, scheduler bug, stalled run) becomes a
    # one-line error message instead of a traceback.
    try:
        # Bad sizes (e.g. --elevators 0) are reported by BuildingConfig itself; a scheduler
        # that cannot run on this fleet (express on one car) is reported by create().
        config = BuildingConfig(
            num_elevators=args.elevators,
            num_floors=args.floors,
            capacity=args.capacity,
            stop_time=args.stop_time,
        )
        requests = read_requests(args.requests)
        sim = Simulation(config, create(args.scheduler, config), requests)
        result = sim.run(max_ticks=args.max_ticks)
    except (OSError, ValueError, SchedulingError, SimulationStalled) as e:
        print(f"elevator-sim: {e}", file=sys.stderr)
        return 1

    # Success: write both logs, then print a summary. An unwritable path (missing
    # directory, no permission) is reported the same way as a bad input file.
    try:
        write_positions_log(args.positions_out, result.positions_log)
        write_passenger_log(args.passengers_out, result.passengers)
    except OSError as e:
        print(f"elevator-sim: {e}", file=sys.stderr)
        return 1
    print(f"Simulated {result.ticks} ticks with {config.num_elevators} elevator(s), "
          f"{config.num_floors} floors, scheduler {args.scheduler!r}.")
    # summarize() refuses an empty run, so handle an empty request file separately.
    if result.passengers:
        print(format_summary(summarize(result.passengers)))
        print(format_fleet(fleet_summary(result.passengers, result.positions_log)))
    else:
        print("No passengers.")
    print(f"Positions log: {args.positions_out}\nPassenger log: {args.passengers_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
