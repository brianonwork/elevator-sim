
import pytest
from conftest import SAMPLE_REQUESTS as SAMPLE
from conftest import IdleScheduler

from elevator_sim.algorithms import BUILTINS, available
from elevator_sim.cli import main


def run_cli(tmp_path, *flags, elevators="2", floors="60"):
    """The CLI on the sample file, with output paths that tests do not read."""
    return main([
        str(SAMPLE), "--elevators", elevators, "--floors", floors, *flags,
        "--positions-out", str(tmp_path / "p.csv"),
        "--passengers-out", str(tmp_path / "x.csv"),
    ])


def test_runs_end_to_end_and_writes_outputs(tmp_path, capsys):
    positions = tmp_path / "pos.csv"
    passengers = tmp_path / "pax.csv"
    code = main([
        str(SAMPLE), "--elevators", "2", "--floors", "60",
        "--positions-out", str(positions), "--passengers-out", str(passengers),
    ])
    assert code == 0
    assert positions.read_text().splitlines()[0] == "time,elevator_0,elevator_1"
    assert positions.read_text().splitlines()[1] == "0,1,1"
    assert len(passengers.read_text().splitlines()) == 4  # header + 3 passengers
    out = capsys.readouterr().out
    assert "Passengers served: 3" in out
    # The fleet lines: one per car, then the peak queue.
    rows = {line.split()[0]: line.split()[1:] for line in out.splitlines()
            if line.startswith("elevator_")}
    assert rows == {"elevator_0": ["2", "100%"], "elevator_1": ["1", "76%"]}
    assert "Peak waiting: 1 passenger(s) at tick 10" in out


def test_a_duplicate_id_exits_1(tmp_path, capsys):
    path = tmp_path / "dup.csv"
    path.write_text("time,id,source,dest\n0,p,1,2\n1,p,2,3\n")
    assert main([str(path), "--positions-out", str(tmp_path / "p.csv"),
                 "--passengers-out", str(tmp_path / "x.csv")]) == 1
    assert "'p' appears more than once" in capsys.readouterr().err


def test_unknown_scheduler_name_is_rejected_by_argparse():
    with pytest.raises(SystemExit) as exc:
        main([str(SAMPLE), "--scheduler", "bogus"])
    assert exc.value.code == 2


def test_requests_outside_building_exit_1(tmp_path, capsys):
    code = main([str(SAMPLE), "--floors", "10", "--positions-out", str(tmp_path / "p.csv")])
    assert code == 1
    assert "outside 1..10" in capsys.readouterr().err


def test_missing_input_file_exits_1(tmp_path, capsys):
    code = main([str(tmp_path / "nope.csv")])
    assert code == 1
    assert "nope.csv" in capsys.readouterr().err


def test_stalled_run_exits_1(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(BUILTINS, "stuck", lambda cfg: IdleScheduler())
    code = main([str(SAMPLE), "--floors", "60", "--max-ticks", "50", "--scheduler", "stuck",
                 "--positions-out", str(tmp_path / "p.csv")])
    assert code == 1
    assert "50 ticks" in capsys.readouterr().err


def test_a_replay_that_does_not_terminate_exits_1(tmp_path, capsys, monkeypatch):
    # The default scheduler predicts drop-offs by replaying the controller; a controller bug
    # shows up there first, as a RuntimeError, before the engine's own stall guard.
    def never_ends(*args, **kwargs):
        raise RuntimeError("controller replay did not terminate")

    monkeypatch.setattr("elevator_sim.algorithms.policies.simulate", never_ends)
    assert run_cli(tmp_path) == 1
    assert capsys.readouterr().err == "elevator-sim: controller replay did not terminate\n"


# -- fleet configuration flags ---------------------------------------------------------


def test_every_fleet_flag_reaches_the_config(tmp_path, monkeypatch):
    """Each flag is checked against a non-default value, so ignoring one fails here."""
    seen = []
    monkeypatch.setitem(
        BUILTINS, "spy", lambda cfg: (seen.append(cfg), IdleScheduler())[1]
    )
    code = run_cli(tmp_path, "--capacity", "3", "--stop-time", "3",
                   "--max-ticks", "5", "--scheduler", "spy", elevators="2", floors="60")
    assert code == 1  # the idle scheduler stalls; we only care that the config was built
    [cfg] = seen
    assert (cfg.num_elevators, cfg.num_floors, cfg.capacity, cfg.stop_time) == (2, 60, 3, 3)


def test_express_scheduler_runs_end_to_end(tmp_path, capsys):
    code = run_cli(tmp_path, "--scheduler", "express", "--stop-time", "1")
    assert code == 0
    assert "scheduler 'express'" in capsys.readouterr().out


def test_express_on_one_car_is_a_one_line_error(tmp_path, capsys):
    code = run_cli(tmp_path, "--scheduler", "express", elevators="1")
    assert code == 1
    assert "at least two cars" in capsys.readouterr().err


# -- built-in schedulers ---------------------------------------------------------------

BUILTIN_NAMES = available()
"""Whatever the registry holds, so a new built-in is smoke-tested through the CLI."""


def test_eta_cost_is_the_cli_default(tmp_path, capsys):
    code = run_cli(tmp_path)
    assert code == 0
    assert "scheduler 'eta-cost'" in capsys.readouterr().out


@pytest.mark.parametrize("name", BUILTIN_NAMES)
def test_each_builtin_runs_the_sample(tmp_path, capsys, name):
    code = run_cli(tmp_path, "--scheduler", name)
    assert code == 0
    assert "Passengers served: 3" in capsys.readouterr().out


def test_a_malformed_request_row_exits_1(tmp_path, capsys):
    bad = tmp_path / "bad.csv"
    bad.write_text("time,id,source,dest\n0,p1,1\n")
    code = main([str(bad), "--positions-out", str(tmp_path / "p.csv"),
                 "--passengers-out", str(tmp_path / "x.csv")])
    assert code == 1
    assert "elevator-sim:" in capsys.readouterr().err


def test_an_unwritable_output_path_is_a_one_line_error(tmp_path, capsys):
    code = main([
        str(SAMPLE), "--elevators", "2", "--floors", "60",
        "--positions-out", str(tmp_path / "missing" / "p.csv"),
        "--passengers-out", str(tmp_path / "x.csv"),
    ])
    assert code == 1
    assert capsys.readouterr().err.startswith("elevator-sim: ")
