"""Running one cell, and resuming a run of the experiments rather than restarting it."""

import csv

import pytest

from elevator_sim.trials import runner
from elevator_sim.trials.paired import paired_diff
from elevator_sim.trials.presets import BY_NAME, BY_NUMBER, rate_for, traffic_for
from elevator_sim.trials.runner import (
    POWERS,
    SCHEDULERS,
    Cell,
    cells,
    done_cells,
    done_series,
    run_all,
    run_cell,
    run_fairness,
    source_changed,
    stale_cells,
    stamp_path,
    variant_of,
)

B2 = BY_NAME["B2"]  # the smallest building: 2 cars, 200 requests


class TestRunCell:
    def test_a_cell_reduces_to_one_row_of_metrics(self):
        preset = BY_NUMBER[1]  # baseline
        cell = Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])
        row = run_cell(cell)
        assert row["served"] > 100 and row["unserved"] == 0
        assert 0 <= row["wait_min"] <= row["wait_p95"] <= row["wait_max"]
        assert row["total_mean"] >= row["wait_mean"]  # total = wait + travel
        assert row["seconds"] > 0


    def test_the_overload_cell_stops_arrivals_at_its_horizon_then_drains(self):
        preset = BY_NUMBER[7]
        seed = preset.seeds(B2)[0]
        requests, _ = traffic_for(preset, B2, rate_for(preset, B2), seed)
        assert requests and max(r.time for r in requests) < preset.horizon
        row = run_cell(Cell(preset.name, "B2", "plain", "round-robin", seed))
        # Everyone who arrived is delivered, so nothing is left to censor.
        assert row["unserved"] == 0
        # served counts every rider in the run: nothing is trimmed from its start.
        assert row["served"] == len(requests)

    def test_a_cell_carries_its_series_back_to_the_parent(self):
        """Workers return the series rather than writing it, so nothing interleaves."""
        preset = BY_NUMBER[7]
        cell = Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])
        series = run_cell(cell)["_series"]
        assert len(series) > 1
        assert [s["window_start"] for s in series] == sorted(s["window_start"] for s in series)
        assert all(s["scheduler"] == "round-robin" for s in series)

    def test_every_preset_carries_its_series(self):
        """Baseline is judged on one number, but still records the per-window series."""
        preset = BY_NUMBER[1]
        cell = Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])
        series = run_cell(cell)["_series"]
        assert series
        assert [s["window_start"] for s in series] == sorted(s["window_start"] for s in series)

    def test_load_override_scales_the_arrival_rate(self):
        # baseline is request-count-bounded (no horizon), so every request is eventually
        # served at either rate and "served" cannot distinguish them; total_mean, which
        # rises monotonically with congestion, is the metric that actually moves.
        cell = Cell("baseline", "B1", "plain", "eta-cost", seed=10_000)
        at_default = run_cell(cell)
        at_double = run_cell(cell, load=2 * BY_NUMBER[1].load)
        assert at_double["total_mean"] > at_default["total_mean"]

    def test_every_scheduler_sees_the_same_requests(self):
        preset = BY_NUMBER[1]
        seed = preset.seeds(B2)[0]
        counts = {
            s: run_cell(Cell(preset.name, "B2", "plain", s, seed))["served"]
            for s in ("round-robin", "nearest-car")
        }
        assert len(set(counts.values())) == 1  # same file in, so the same people served


class TestRunAll:
    def test_cells_cover_every_scheduler_and_seed(self):
        preset = BY_NUMBER[1]
        listed = list(cells([preset], [B2]))
        # Every scheduler, in both big-bang experiments (free stops and 1-tick stops).
        assert len(listed) == 2 * len(SCHEDULERS) * B2.seeds
        assert {c.variant for c in listed} == {"plain", "stop1"}
        assert {c.seed for c in listed} == set(preset.seeds(B2))

    def test_a_run_writes_a_consolidated_series_file(self, tmp_path):
        preset = BY_NUMBER[7]
        path = tmp_path / "runs.csv"
        cell = Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])
        run_all([cell], path=path, jobs=1)
        with open(tmp_path / "runs-windows.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows and {r["preset"] for r in rows} == {"overload"}

    def test_a_done_cell_without_series_gets_them_backfilled(self, tmp_path):
        """A results row from before every preset recorded a series keeps its row."""
        preset = BY_NUMBER[1]
        path = tmp_path / "runs.csv"
        cell = Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])
        run_all([cell], path=path, jobs=1)
        original = path.read_text()
        (tmp_path / "runs-windows.csv").unlink()

        result = run_all([cell], path=path, jobs=1)
        assert (result.written, result.series_backfilled) == (0, 1)
        assert path.read_text() == original  # results row untouched, not duplicated
        assert done_series(tmp_path / "runs-windows.csv") == {
            (cell.preset, cell.building, cell.variant, cell.scheduler, cell.seed)}

    def test_a_done_cell_with_series_only_gets_its_results_row(self, tmp_path):
        """The reverse gap: an interrupted run left series rows but no results row."""
        preset = BY_NUMBER[1]
        path = tmp_path / "runs.csv"
        cell = Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])
        run_all([cell], path=path, jobs=1)
        series = (tmp_path / "runs-windows.csv").read_text()
        path.unlink()

        assert run_all([cell], path=path, jobs=1).written == 1
        assert (tmp_path / "runs-windows.csv").read_text() == series  # no duplicate series rows

    def test_a_finished_cell_is_not_run_again(self, tmp_path):
        preset = BY_NUMBER[1]
        path = tmp_path / "runs.csv"
        todo = [Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])]

        assert run_all(todo, path=path, jobs=1).written == 1
        assert run_all(todo, path=path, jobs=1).written == 0  # resumed, not redone
        with open(path, newline="") as f:
            assert len(list(csv.DictReader(f))) == 1

    def test_done_cells_reads_back_what_was_written(self, tmp_path):
        preset = BY_NUMBER[1]
        path = tmp_path / "runs.csv"
        cell = Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])
        run_all([cell], path=path, jobs=1)
        assert done_cells(path) == {
            (cell.preset, cell.building, cell.variant, cell.scheduler, cell.seed)}

    def test_an_empty_results_file_means_nothing_is_done(self, tmp_path):
        assert done_cells(tmp_path / "nothing.csv") == set()

    def test_an_existing_but_empty_results_file_still_gets_a_header(self, tmp_path):
        """A zero-byte file exists, so `not path.exists()` is the wrong freshness test.

        Without a header the first data row becomes one, done_cells raises KeyError, and the
        file is unresumable until someone deletes it.
        """
        preset = BY_NUMBER[1]
        path = tmp_path / "runs.csv"
        path.touch()
        (tmp_path / "runs-windows.csv").touch()
        cell = Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])

        assert run_all([cell], path=path, jobs=1).written == 1
        assert path.read_text().splitlines()[0].startswith("preset,building,variant")
        assert done_cells(path) == {
            (cell.preset, cell.building, cell.variant, cell.scheduler, cell.seed)}


class TestRunFailures:
    def test_a_failing_cell_is_counted_and_named(self, tmp_path):
        preset = BY_NUMBER[1]
        path = tmp_path / "runs.csv"
        good = Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])
        # No such scheduler, so this cell raises KeyError inside the worker.
        bad = Cell(preset.name, "B2", "plain", "no-such-scheduler", preset.seeds(B2)[0])

        result = run_all([good, bad], path=path, jobs=1)
        assert result.written == 1
        assert len(result.failures) == 1
        assert "no-such-scheduler" in result.failures[0]

    def test_a_clean_run_reports_no_failures(self, tmp_path):
        preset = BY_NUMBER[1]
        cell = Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])
        result = run_all([cell], path=tmp_path / "r.csv", jobs=1)
        assert (result.written, result.failures) == (1, [])


def double_span(path):
    """Rewrite a one-row results file with its ``span`` doubled, as a rate change would."""
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames, rows = reader.fieldnames, list(reader)
    rows[0]["span"] = str(int(rows[0]["span"]) * 2)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class TestStaleCells:
    """``stale_cells`` reads staleness off ``span`` rather than a dedicated ``rate`` column:
    adding one would grow ``COLUMNS`` past the 19 fields the committed ``runs.csv`` header
    already has, and ``run_all`` only ever appends with that fixed set of fields."""

    def test_a_row_generated_at_a_different_rate_is_flagged_by_its_span(self, tmp_path):
        preset = BY_NUMBER[1]  # baseline: request-bounded, so span is derived from the rate
        path = tmp_path / "runs.csv"
        cell = Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])
        run_all([cell], path=path, jobs=1)
        line = f"{preset.name}/B2/round-robin/{cell.seed}"

        # Generated at the current rate: its span still matches, not stale.
        assert stale_cells(path) == []
        # A row whose span implies a different rate (as after a demand change): flagged.
        double_span(path)
        assert stale_cells(path) == [line]

    def test_overload_rows_are_skipped_since_their_span_is_the_horizon_not_the_rate(
        self, tmp_path
    ):
        preset = BY_NUMBER[7]  # overload: span is always the tick horizon, rate-independent
        path = tmp_path / "runs.csv"
        cell = Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])
        run_all([cell], path=path, jobs=1)
        # Doubling the recorded span would flag a request-bounded row; overload is skipped.
        double_span(path)
        assert stale_cells(path) == []

    def test_a_missing_results_file_has_nothing_stale(self, tmp_path):
        assert stale_cells(tmp_path / "nothing.csv") == []


class TestSourceStamp:
    """Resume keys on cell identity, so results recorded under different code look finished.
    The stamp beside ``runs.csv`` is what lets ``run`` say so."""

    @staticmethod
    def one_cell():
        preset = BY_NUMBER[1]
        return Cell(preset.name, "B2", "plain", "round-robin", preset.seeds(B2)[0])

    def test_a_fresh_run_is_stamped_and_reads_as_current(self, tmp_path):
        path = tmp_path / "runs.csv"
        run_all([self.one_cell()], path=path, jobs=1)
        assert stamp_path(path).read_text().strip() == runner.source_fingerprint()
        assert not source_changed(path)

    def test_results_recorded_under_other_code_are_flagged_and_keep_their_stamp(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "runs.csv"
        run_all([self.one_cell()], path=path, jobs=1)
        recorded = stamp_path(path).read_text()
        monkeypatch.setattr(runner, "source_fingerprint", lambda: "edited-since")

        assert source_changed(path)
        # The cell still counts as done, which is exactly why the warning has to exist.
        assert run_all([self.one_cell()], path=path, jobs=1).written == 0
        assert stamp_path(path).read_text() == recorded

    def test_results_with_no_stamp_are_flagged(self, tmp_path):
        path = tmp_path / "runs.csv"
        run_all([self.one_cell()], path=path, jobs=1)
        stamp_path(path).unlink()
        assert source_changed(path)

    def test_no_results_means_nothing_to_flag(self, tmp_path):
        assert not source_changed(tmp_path / "nothing.csv")

    def test_the_fingerprint_ignores_the_report_and_the_command_lines(self):
        hashed = {p.as_posix() for p in runner.fingerprinted_files()}
        assert "trials/runner.py" in hashed and "algorithms/controller.py" in hashed
        assert not hashed & {"trials/report.py", "trials/cli.py", "cli.py"}


def test_an_unknown_variant_name_is_a_key_error_naming_the_choices():
    with pytest.raises(KeyError, match="no variant 'stop9'.*plain.*stop1"):
        variant_of(BY_NUMBER[1], B2, "stop9")


class TestFairness:
    def test_one_row_per_power_and_seed(self, tmp_path):
        path = tmp_path / "exponent_sweep.csv"
        written = run_fairness(path=path, seeds=2)
        with open(path) as f:
            rows = list(csv.DictReader(f))
        assert written == len(rows) == 2 * len(POWERS)
        assert {r["power"] for r in rows} == {str(p) for p in POWERS}
        assert len({r["seed"] for r in rows}) == 2

    def test_the_rows_support_a_paired_comparison(self, tmp_path):
        """The point of per-seed rows: the pre-registered test becomes recomputable."""
        path = tmp_path / "exponent_sweep.csv"
        run_fairness(path=path, seeds=3)
        with open(path) as f:
            rows = list(csv.DictReader(f))
        by_power = {}
        for r in rows:
            by_power.setdefault(float(r["power"]), {})[int(r["seed"])] = float(r["total_mean"])
        assert paired_diff(by_power[1.0], by_power[2.0]).n == 3
