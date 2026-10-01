"""The ``elevator-trials`` command line: where it reads and writes its results."""

from elevator_sim.trials.cli import main


def test_run_refuses_to_start_where_there_is_no_results_directory(tmp_path, monkeypatch, capsys):
    # Away from the repository root, the default would start all 7,910 cells afresh in a new
    # docs/ tree. Say so instead, and create nothing.
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--preset", "baseline", "--building", "B2"]) == 1
    assert "pass --results-dir" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_report_away_from_the_results_says_what_to_do(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["report", "--no-figures"]) == 1
    assert "runs.csv not found" in capsys.readouterr().err


def test_run_then_report_in_a_directory_of_the_callers_choosing(tmp_path, monkeypatch, capsys):
    # Nothing is read from or written to the working directory.
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    out = tmp_path / "out"
    scope = ["--preset", "baseline", "--building", "B2"]

    assert main(["--results-dir", str(out), "run", *scope, "--jobs", "4"]) == 0
    assert "140 new rows" in capsys.readouterr().out
    assert (out / "data" / "runs.csv").exists() and (out / "data" / "runs-windows.csv").exists()

    assert main(["--results-dir", str(out), "run", *scope, "--jobs", "4"]) == 0
    printed = capsys.readouterr().out
    assert "0 new rows" in printed and "warning" not in printed

    assert main(["--results-dir", str(out), "report", "--no-figures"]) == 0
    assert "#### 1. baseline" in (out / "report.md").read_text()
    assert list(elsewhere.iterdir()) == []
