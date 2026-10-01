"""Reading results rows into per-preset tables, winners, and the ranking-stability roll-up."""


import pytest

from elevator_sim.trials.presets import BUILDINGS, BY_NUMBER, PRESETS
from elevator_sim.trials.report import (
    FASTER,
    NO_DIFFERENCE,
    TRADED,
    cell,
    column_seed_values,
    express_table,
    fairness_table,
    fairness_tradeoff,
    fairness_tradeoff_table,
    group,
    headline_variant,
    leaders,
    pattern_rows,
    preset_table,
    ranking_table,
    read_rows,
    seed_values,
    uneven_cells,
    value,
    write_figures,
)
from elevator_sim.trials.runner import COLUMNS, SCHEDULERS

BASELINE = BY_NUMBER[1]      # request-bounded
SKEWED = BY_NUMBER[4]        # request-bounded, used where a second preset is needed
OVERLOAD = BY_NUMBER[7]      # arrivals tick-bounded, then drained like every other run


def row(preset, building, scheduler, seed=0, variant="plain", **metrics):
    base = dict.fromkeys(COLUMNS, 0)
    base.update(
        preset=preset.name, building=building, variant=variant, scheduler=scheduler, seed=seed,
        served=100, unserved=0, total_mean=10.0, total_p95=20, total_max=30, seconds=0.1,
    )
    base.update(metrics)
    return base


class TestValue:
    def test_reads_the_mean_total_time(self):
        assert value(row(BASELINE, "B1", "eta-cost", total_mean=12.5)) == 12.5

    def test_overload_is_judged_on_the_same_plain_mean(self):
        assert value(row(OVERLOAD, "B1", "eta-cost", total_mean=128.3)) == 128.3


class TestUnevenCells:
    def test_a_cell_whose_arms_share_a_seed_set_is_not_flagged(self):
        rows = [row(BASELINE, "B1", s, seed=i) for s in ("eta-cost", "nearest-car")
                for i in (1, 2)]
        assert uneven_cells(rows) == []

    def test_a_cell_missing_a_seed_in_one_arm_is_flagged(self):
        rows = [row(BASELINE, "B1", "eta-cost", seed=i) for i in (1, 2)]
        rows += [row(BASELINE, "B1", "nearest-car", seed=1)]
        [flagged] = uneven_cells(rows)
        assert "baseline" in flagged and "B1" in flagged

    def test_a_cell_missing_a_scheduler_entirely_is_flagged(self):
        rows = [row(BASELINE, "B1", s, seed=i) for s in ("eta-cost", "nearest-car")
                for i in (1, 2)]
        rows += [row(BASELINE, "B2", "eta-cost", seed=i) for i in (1, 2)]
        [flagged] = uneven_cells(rows)
        assert "B2" in flagged and "nearest-car=0" in flagged



class TestCellAndWinner:
    def test_a_cell_reports_the_mean_and_spread_across_seeds(self):
        rows = [row(BASELINE, "B1", "eta-cost", seed=i, total_mean=v)
                for i, v in enumerate((10.0, 20.0, 30.0))]
        assert cell(rows) == (20.0, 10.0, 30.0)

    def test_a_cell_with_nothing_in_it_is_none(self):
        assert cell([]) is None

    def test_the_fastest_scheduler_wins_on_a_time_metric(self):
        by_seed = {
            "eta-cost": {0: 9.0, 1: 10.0, 2: 11.0},
            "round-robin": {0: 19.0, 1: 20.0, 2: 21.0},
        }
        assert leaders(by_seed) == ["eta-cost"]

    def test_an_empty_cell_has_no_winner(self):
        assert leaders({"eta-cost": {}}) == []

    def test_the_winner_column_lists_the_leader_first(self):
        # Two arms within noise of each other, eta-cost-fair ahead on the mean.
        rows = [row(BASELINE, "B1", "eta-cost", seed=i, total_mean=v)
                for i, v in enumerate([10.0, 12.0, 11.0])]
        rows += [row(BASELINE, "B1", "eta-cost-fair", seed=i, total_mean=v)
                 for i, v in enumerate([9.0, 13.0, 10.0])]
        by_seed = {s: seed_values([r for r in rows if r["scheduler"] == s])
                   for s in ("eta-cost", "eta-cost-fair")}
        assert leaders(by_seed)[0] == "eta-cost-fair"


class TestTables:
    def test_the_preset_table_names_the_winner_per_building(self):
        rows = [row(BASELINE, "B1", "eta-cost", seed=i, total_mean=5.0) for i in range(2)]
        rows += [row(BASELINE, "B1", "round-robin", seed=i, total_mean=9.0) for i in range(2)]
        lines = preset_table(rows, BASELINE)
        assert any(line.startswith("| B1 |") and line.rstrip().endswith("eta-cost |")
                   for line in lines)

    def test_a_building_with_no_rows_is_left_out(self):
        rows = [row(BASELINE, "B1", "eta-cost", total_mean=5.0)]
        assert not any(line.startswith("| B3 |") for line in preset_table(rows, BASELINE))

    def test_each_experiment_reads_only_its_own_variant(self):
        # Big-bang-0 (plain) favours eta-cost; big-bang-1 (stop1) favours round-robin.
        rows = [row(BASELINE, "B1", "eta-cost", seed=i, total_mean=5.0) for i in range(2)]
        rows += [row(BASELINE, "B1", "round-robin", seed=i, total_mean=9.0) for i in range(2)]
        rows += [row(BASELINE, "B1", "eta-cost", seed=i, variant="stop1", total_mean=9.0)
                 for i in range(2)]
        rows += [row(BASELINE, "B1", "round-robin", seed=i, variant="stop1", total_mean=5.0)
                 for i in range(2)]
        assert preset_table(rows, BASELINE)[2].rstrip().endswith("eta-cost |")
        assert preset_table(rows, BASELINE, "stop1")[2].rstrip().endswith("round-robin |")

    def test_the_express_table_is_the_pair_only_not_the_1_tick_run(self):
        express = BY_NUMBER[8]
        rows = [row(express, "B1", "express", seed=1, variant=v)
                for v in ("plain", "stop1", "stop3")]
        variants = {line.split("|")[1].strip() for line in express_table(rows)[2:]}
        assert variants == {"plain", "stop3"}

    def test_the_ranking_table_bolds_a_winner_that_differs_from_b1(self):
        rows = [
            *[row(BASELINE, "B1", "eta-cost", seed=i, total_mean=5.0) for i in range(2)],
            *[row(BASELINE, "B1", "round-robin", seed=i, total_mean=9.0) for i in range(2)],
            *[row(BASELINE, "B2", "eta-cost", seed=i, total_mean=9.0) for i in range(2)],
            *[row(BASELINE, "B2", "round-robin", seed=i, total_mean=5.0) for i in range(2)],
        ]  # the flip
        line = next(ln for ln in ranking_table(rows) if ln.startswith("| baseline |"))
        assert "| eta-cost |" in line and "**round-robin**" in line


def test_the_express_headline_variant_is_comparable_across_buildings():
    from elevator_sim.trials.presets import BY_NAME
    express = BY_NUMBER[8]
    assert headline_variant(express, BY_NAME["B1"]) == "plain"
    assert headline_variant(express, BY_NAME["B2"]) == "plain"


class TestPatternPages:
    def test_every_page_shows_the_same_rows_judged_number_first(self):
        for preset in (BASELINE, SKEWED, OVERLOAD):
            rows = pattern_rows(preset)
            assert rows[0].answers and rows[0].column == "total_mean"
            assert [r.column for r in rows] == [
                "total_mean", "total_p95", "total_max", "wait_mean"]

    def test_seed_values_read_a_column(self):
        rows = [row(SKEWED, "B1", "eta-cost", seed=3, total_p95=20)]
        assert column_seed_values(rows, "total_p95") == {3: 20.0}

    def test_leaders_prefer_the_lower_number(self):
        by_seed = {"eta-cost": {s: 10.0 + s % 2 for s in range(10)},
                   "round-robin": {s: 50.0 + s % 2 for s in range(10)}}
        assert leaders(by_seed)[0] == "eta-cost"


def test_the_express_table_names_the_mean_and_the_worst_seed_separately():
    express = BY_NUMBER[8]
    rows = [row(express, "B1", "eta-cost", seed=1, variant="plain",
                total_mean=90.0, total_p95=95, total_max=100),
            row(express, "B1", "eta-cost", seed=2, variant="plain",
                total_mean=80.0, total_p95=85, total_max=20)]
    lines = express_table(rows)
    assert "max total (worst seed)" in lines[0]
    assert "max total (mean)" in lines[0]
    # 100 is the worst seed's; 60.0 is the mean of 100 and 20.
    assert "| plain | eta-cost | 85.0 | 90.0 | 60.0 | 100 |" in lines[-1]


def test_reading_a_missing_results_file_says_what_to_run(tmp_path):
    with pytest.raises(FileNotFoundError, match="run"):
        read_rows(tmp_path / "nothing.csv")


class TestNoiseFloor:
    def test_a_lead_inside_the_seed_spread_keeps_both_schedulers(self):
        # eta-cost leads by 0.5 on average, but the per-seed gap is 1, 0.5, 0: noise, not a result.
        by_seed = {
            "eta-cost": {0: 8.0, 1: 10.0, 2: 12.0},
            "round-robin": {0: 9.0, 1: 10.5, 2: 12.0},
        }
        assert leaders(by_seed) == ["eta-cost", "round-robin"]

    def test_a_lead_clear_of_the_spread_stands_alone(self):
        by_seed = {
            "eta-cost": {0: 9.5, 1: 10.0, 2: 10.5},
            "round-robin": {0: 19.0, 1: 20.0, 2: 21.0},
        }
        assert leaders(by_seed) == ["eta-cost"]

    def test_a_tie_between_the_leaders_still_excludes_a_clear_loser(self):
        by_seed = {
            "eta-cost": {0: 8.0, 1: 10.0, 2: 12.0},
            "eta-cost-fair": {0: 9.0, 1: 10.5, 2: 12.0},
            "round-robin": {0: 39.0, 1: 40.0, 2: 41.0},
        }
        assert leaders(by_seed) == ["eta-cost", "eta-cost-fair"]

    def test_a_tied_pair_is_not_reported_as_a_rank_flip(self):
        rows = [row(BASELINE, b, s, seed=i, total_mean=m)
                for b in ("B1", "B2")
                for s, ms in (("eta-cost", (10.0, 14.0)), ("round-robin", (11.0, 13.0)))
                for i, m in enumerate(ms)]
        line = next(ln for ln in ranking_table(rows) if ln.startswith("| baseline |"))
        assert "**" not in line

    def test_a_same_tie_set_in_a_different_order_is_not_bolded(self):
        # B1 and B2 tie the same pair (eta-cost, eta-cost-fair) against a clear loser, but
        # which one edges out the other on the raw mean flips between buildings. leaders()
        # puts the leader first, so the two winner lists differ in order only -- that used
        # to bold the cell under list equality even though the tie *set* never changed.
        rows = [
            row(BASELINE, b, s, seed=i, total_mean=m)
            for b, values in (
                ("B1", {"eta-cost": (8.0, 10.0, 12.0), "eta-cost-fair": (9.0, 10.5, 12.0)}),
                ("B2", {"eta-cost": (9.0, 10.5, 12.0), "eta-cost-fair": (8.0, 10.0, 12.0)}),
            )
            for s, ms in {**values, "round-robin": (39.0, 40.0, 41.0)}.items()
            for i, m in enumerate(ms)
        ]
        by_seed = {
            s: seed_values([r for r in rows if r["building"] == "B1" and r["scheduler"] == s])
            for s in ("eta-cost", "eta-cost-fair", "round-robin")
        }
        assert leaders(by_seed) == ["eta-cost", "eta-cost-fair"]
        by_seed = {
            s: seed_values([r for r in rows if r["building"] == "B2" and r["scheduler"] == s])
            for s in ("eta-cost", "eta-cost-fair", "round-robin")
        }
        assert leaders(by_seed) == ["eta-cost-fair", "eta-cost"]
        line = next(ln for ln in ranking_table(rows) if ln.startswith("| baseline |"))
        assert "**" not in line


def test_the_fairness_table_averages_the_per_seed_rows():
    rows = [{"power": "1.0", "seed": "1", "total_mean": "10.0", "total_p95": "20",
             "total_max": "30"},
            {"power": "1.0", "seed": "2", "total_mean": "20.0", "total_p95": "40",
             "total_max": "50"}]
    [header_row, separator, line] = fairness_table(rows)
    assert line == "| 1.0 | 15.0 | 30.0 | 40.0 | 2 |"


class TestFairnessTradeoff:
    """``eta-cost-fair`` against ``eta-cost``, per cell, on average and 95-in-100."""

    @staticmethod
    def cell(plain, fair, seeds=(1, 2, 3)):
        """One cell: ``plain``/``fair`` map a seed to its (total_mean, total_p95)."""
        rows = []
        for scheduler, values in (("eta-cost", plain), ("eta-cost-fair", fair)):
            for seed in seeds:
                average, tail = values(seed)
                rows.append(row(BASELINE, "B1", scheduler, seed=seed, total_mean=average,
                                total_p95=tail))
        return group(rows)

    def test_a_fair_arm_faster_on_every_seed_is_better_on_the_average(self):
        # Fair is 10% faster on the average, with a little spread; the tail is identical.
        grouped = self.cell(lambda s: (100.0, 200), lambda s: (90.0 - s * 0.1, 200))
        [trade] = fairness_tradeoff(grouped)
        assert trade.average_verdict == "better" and trade.tail_verdict == "same"
        assert trade.average == pytest.approx(-10.2)
        assert trade.separated and not trade.trade and not trade.identical

    def test_a_shorter_tail_bought_with_a_longer_average_is_a_trade(self):
        grouped = self.cell(lambda s: (100.0, 200), lambda s: (110.0 + s * 0.1, 180 - s))
        [trade] = fairness_tradeoff(grouped)
        assert (trade.average_verdict, trade.tail_verdict) == ("worse", "better")
        assert trade.trade

    def test_identical_arms_are_the_same_with_zero_width(self):
        grouped = self.cell(lambda s: (100.0 + s, 200 + s), lambda s: (100.0 + s, 200 + s))
        [trade] = fairness_tradeoff(grouped)
        assert trade.identical and not trade.separated
        assert (trade.average, trade.average_width, trade.tail_width) == (0, 0, 0)

    def test_a_cell_with_one_shared_seed_is_left_out(self):
        grouped = self.cell(lambda s: (100.0, 200), lambda s: (90.0, 190), seeds=(1,))
        assert fairness_tradeoff(grouped) == []

    def test_each_cell_lands_in_one_plain_outcome(self):
        cases = [
            (NO_DIFFERENCE, lambda s: (100.0 + s, 200 + s), lambda s: (100.0 + s, 200 + s)),
            # Fair is a hair faster on one seed only: not separated, so no real difference.
            (NO_DIFFERENCE, lambda s: (100.0 + s, 200), lambda s: (100.0 + s - (s == 1), 200)),
            (TRADED, lambda s: (100.0, 200), lambda s: (110.0 + s * 0.1, 180 - s)),
            (FASTER, lambda s: (100.0, 200), lambda s: (90.0 - s * 0.1, 200)),
        ]
        for expected, plain, fair in cases:
            [trade] = fairness_tradeoff(self.cell(plain, fair))
            assert trade.outcome == expected

    def test_the_table_opens_with_the_counts(self):
        grouped = self.cell(lambda s: (100.0, 200), lambda s: (110.0 + s * 0.1, 180 - s))
        summary, blank, *table = fairness_tradeoff_table(fairness_tradeoff(grouped))
        assert summary.startswith("1 cells: 1 trade")
        assert table[2].startswith("| baseline B1 | +10.20 ± ")


def write_runs(path, rows):
    """Write synthetic rows as a runs.csv the report can read."""
    import csv
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def synthetic_runs(with_pair=True):
    """Two seeds of every cell in both big-bang experiments, with the stop-time pair's 3-tick
    arm on B1 for preset 8 unless told not to."""
    express = BY_NUMBER[8]
    rows = []
    for preset in PRESETS:
        for building in BUILDINGS:
            for variant in ("plain", "stop1"):
                for i, scheduler in enumerate(SCHEDULERS):
                    for seed in (1, 2):
                        rows.append(row(preset, building.name, scheduler, seed=seed,
                                        variant=variant, total_mean=10.0 + i,
                                        total_p95=20 + i, total_max=30 + i, wait_mean=5.0 + i))
    if with_pair:
        for i, scheduler in enumerate(SCHEDULERS):
            for seed in (1, 2):
                rows.append(row(express, "B1", scheduler, seed=seed, variant="stop3",
                                total_mean=15.0 + i, total_p95=25 + i, total_max=35 + i,
                                wait_mean=6.0 + i))
    return rows


# Rendering every PNG for two big-bang experiments takes about 10s alone, more under a busy
# suite, so these tests get more than the project's 10s per-test default.
@pytest.mark.timeout(60)
class TestFigures:
    """The PNG writers, on synthetic runs: one directory per experiment."""

    @pytest.fixture(autouse=True)
    def _needs_matplotlib(self):
        pytest.importorskip("matplotlib")

    def test_figures_are_written_one_directory_per_experiment(self, tmp_path):
        results = write_runs(tmp_path / "runs.csv", synthetic_runs())
        made = write_figures(tmp_path / "figures", results)
        folders = [tmp_path / "figures" / name for name in ("big-bang-0", "big-bang-1")]
        for folder in folders:
            assert (folder / "best-schedulers.png").is_file()
            pages = sorted(p.name for p in (folder / "by-traffic").glob("*.png"))
            assert pages == sorted(f"{p.name}.png" for p in PRESETS)
        pair = tmp_path / "figures" / "stop-time-pair" / "local-plus-express-b1.png"
        assert pair.is_file()
        # Every synthetic seed is equal, so every interval has zero width: still drawable.
        trade = tmp_path / "figures" / "fairness-efficiency" / "trade-off.png"
        assert trade.is_file()
        assert len(made) == 2 * (len(PRESETS) + 1) + 2
        pages = {f / "by-traffic" / f"{p.name}.png" for f in folders for p in PRESETS}
        overviews = {f / "best-schedulers.png" for f in folders}
        assert set(made) == {*pages, *overviews, pair, trade}

    def test_the_pair_figure_survives_runs_without_the_stop3_arm(self, tmp_path):
        # A partial run (the pair not run yet) still reports; the stop-3 column is empty.
        results = write_runs(tmp_path / "runs.csv", synthetic_runs(with_pair=False))
        made = write_figures(tmp_path / "figures", results)
        assert (tmp_path / "figures" / "stop-time-pair" / "local-plus-express-b1.png") in made

