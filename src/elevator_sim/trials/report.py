"""Turning results rows into the tables and figures the write-up needs.

``runner.py`` writes one CSV row per simulation run, and this module reads those rows back and
writes ``report.md`` plus a few PNG charts. Terms used below:

- In this module a **cell** means one preset on one building in one variant: the group of
  ``runner.py`` cells (single runs) across every scheduler and **seed** (a seed fixes one
  reproducible request file), one row per scheduler per seed.
- Every cell is **judged on** one number, the mean total time (``JUDGED_ON``): the prompt's
  own objective. Every run drains, overload included, so it covers every passenger.
- The **headline variant** is a preset's first variant, the one compared across buildings.
- A **paired comparison** compares two schedulers seed by seed on the identical traffic,
  so the random draw cancels out; the **tie set** is the leader plus every scheduler whose
  paired difference from it is too small to call.

For each big-bang experiment (every preset on every building, with stops costing 0 ticks in
big-bang-0 and 1 tick in big-bang-1), a table per preset and the ranking-stability roll-up
across buildings; then the stop-time pair (preset 8 at stop time 0 and 3, where the ``express``
scheduler is expected to pay only once stops cost time), the fairness-efficiency trade-off and
the fairness sweep. Every per-preset table reports the mean
across seeds with its min-max spread printed beneath it, but that spread is context, not
the tie-breaking statistic: every scheduler ran the identical request file at a given seed,
so the honest comparison is the paired per-seed difference against the leader (see
`paired.py`), and a challenger only leaves the tie set once that difference clears the
two-sided 95% t-critical value for its own degrees of freedom (`leaders()`).
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from .paired import paired_diff
from .presets import (
    BUILDINGS,
    BY_NUMBER,
    EXPERIMENTS,
    PLAIN,
    PRESETS,
    REFERENCE_BUILDING,
    STOP1,
    STOP3,
    Building,
    Preset,
    rate_for,
)
from .runner import FAIRNESS, FAIRNESS_PRESET, RESULTS, SCHEDULERS

JUDGED_ON = "total_mean"
"""The one ``runs.csv`` column every cell is judged on: mean total time, request to drop-off,
over every passenger in the run."""
REPORT = Path("docs/results/report.md")
"""Where ``write_report`` writes the markdown report."""
FIGURES = Path("docs/results/figures")
"""Directory ``write_figures`` writes its PNGs into, one subdirectory per experiment."""
BY_TRAFFIC = "by-traffic"
"""Subfolder of each big-bang experiment's figure folder (``FIGURES / name``) holding one page
per traffic pattern (``write_pattern_pages``); the overview sits beside it."""
STOP_TIME_PAIR = (PLAIN.name, STOP3.name)
"""The stop-time pair's two arms on preset 8 at B1: free stops, then 3-tick stops. Named
explicitly because that cell also has big-bang-1's 1-tick run, which is not part of the pair."""
STOP_TIME_DIR = FIGURES / "stop-time-pair"
"""Experiment 2, the stop-time pair: preset 8 on B1 at stop time 0 and 3
(``write_stop_time_pair``)."""
FAIRNESS_TRADE_DIR = FIGURES / "fairness-efficiency"
"""Experiment 3, the fairness–efficiency trade-off: ``eta-cost-fair`` against ``eta-cost``
in every cell both ran (``write_fairness_tradeoff``)."""


@dataclass(frozen=True)
class HeatmapNumber:
    """One ``runs.csv`` column, with the title and unit the pattern pages label it by.

    Named for the heatmaps it was written for; those are retired, but ``pattern_rows``
    still reads its titles and units.
    """

    slug: str
    """Short name, e.g. ``"trip_avg"``; it named the retired ``heatmap_trip_avg.png``."""
    column: str
    """The ``runs.csv`` column read, e.g. ``"total_mean"``."""
    title: str
    """Figure title, matching the summary's numbers table."""
    unit: str
    """Unit of the column: ``"ticks"`` or ``"seconds"``. Every number is better smaller."""


HEATMAP_NUMBERS = (
    HeatmapNumber("trip_avg", "total_mean", "Average trip time", "ticks"),
    HeatmapNumber("trip_p95", "total_p95", "95-in-100 trip time", "ticks"),
    HeatmapNumber("trip_max", "total_max", "Longest trip time", "ticks"),
    HeatmapNumber("trip_min", "total_min", "Shortest trip time", "ticks"),
    HeatmapNumber("wait_avg", "wait_mean", "Average wait", "ticks"),
    HeatmapNumber("wait_p95", "wait_p95", "95-in-100 wait", "ticks"),
    HeatmapNumber("wait_max", "wait_max", "Longest wait", "ticks"),
    HeatmapNumber("wait_min", "wait_min", "Shortest wait", "ticks"),
    HeatmapNumber("compute", "seconds", "Computer time", "seconds"),
)
"""Every per-run number in the summary's numbers table, with its title and unit."""

def header(*names: str) -> list[str]:
    """Return a markdown header row and its separator, so the two can never disagree.

    Args:
        names: Column headings, left to right.

    Returns:
        Two lines: the header row and the ``|---|`` separator with one cell per name.
    """
    return [f"| {' | '.join(names)} |", "|" + "---|" * len(names)]


def read_rows(path: Path = RESULTS) -> list[dict]:
    """Read a CSV written by the harness into one dict per row.

    Args:
        path: The CSV to read; defaults to ``runs.csv``, every experiment's runs.

    Returns:
        Every row, keyed by column name. Values are strings, as ``csv`` leaves them.

    Raises:
        FileNotFoundError: ``path`` does not exist (the step that writes it has not run).
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `elevator-trials run` first")
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def value(row: dict) -> float:
    """Return the number the row is judged on, ``JUDGED_ON``.

    Args:
        row: One ``runs.csv`` row.

    Returns:
        The mean total time in ticks.
    """
    return float(row[JUDGED_ON])


def headline_variant(preset: Preset, building: Building) -> str:
    """Return big-bang-0's variant, the one the headline cross-building comparisons use.

    Big-bang-1 and preset 8's stop-time pair get their own tables.

    Args:
        preset: The preset whose variant is wanted.
        building: The building, since some presets have extra variants on B1 only.

    Returns:
        The name of the preset's first variant on this building.
    """
    return preset.variants(building)[0].name


def cell(rows: Sequence[dict]) -> tuple[float, float, float] | None:
    """Return the mean, min and max of the judged number across this cell's seeds.

    Args:
        rows: The cell's rows, one per seed.

    Returns:
        ``(mean, min, max)``; ``None`` when there are no rows.
    """
    values = [value(r) for r in rows]
    if not values:
        return None
    return mean(values), min(values), max(values)


def seed_values(rows: Sequence[dict]) -> dict[int, float]:
    """Return the judged number per seed, which pairs one scheduler against another.

    Args:
        rows: One scheduler's rows for a cell.

    Returns:
        Seed to value.
    """
    return {int(r["seed"]): value(r) for r in rows}


def group(rows: Sequence[dict]) -> dict[tuple, list[dict]]:
    """Group results rows by cell and scheduler, so each group is one scheduler's seeds.

    Args:
        rows: ``runs.csv`` rows.

    Returns:
        ``(preset, building, variant, scheduler)`` to that cell's rows, in file order.
    """
    grouped: dict[tuple, list[dict]] = {}
    for r in rows:
        grouped.setdefault((r["preset"], r["building"], r["variant"], r["scheduler"]), []).append(r)
    return grouped


def cell_rows(
    grouped: dict[tuple, list[dict]], preset: Preset, building: Building,
    variant: str | None = None,
) -> dict[str, list[dict]]:
    """Return each scheduler's rows for this preset and building, in one experiment.

    Args:
        grouped: Rows grouped by cell, as ``group`` returns them.
        preset: The preset to pick out.
        building: The building to pick out.
        variant: The variant naming the experiment (``EXPERIMENTS``' values); ``None`` = the
            headline variant, big-bang-0's.

    Returns:
        Scheduler name to its rows, for every scheduler in ``SCHEDULERS`` order; a
        scheduler with no rows maps to an empty list.
    """
    variant = variant or headline_variant(preset, building)
    return {s: grouped.get((preset.name, building.name, variant, s), []) for s in SCHEDULERS}


@dataclass(frozen=True)
class PatternRow:
    """One row of a per-pattern page: a number read per seed, and whether it is the judged one."""

    label: str
    """Row title, e.g. ``"95-in-100 trip time"``."""
    column: str
    """``runs.csv`` column."""
    unit: str
    """Unit of the number: ``"ticks"`` or ``"seconds"``."""
    answers: bool = False
    """True for the row holding the number every cell is judged on."""


PATTERN_STANDARD_COLUMNS = ("total_p95", "total_max", "wait_mean")
"""Numbers every per-pattern page shows after the judged one."""


def pattern_rows(preset: Preset) -> list[PatternRow]:
    """Return the rows of a per-pattern page: the judged number first, then the rest.

    Args:
        preset: The pattern.

    Returns:
        The mean total time (``JUDGED_ON``), then 95-in-100 and longest trip and average wait.
    """
    by_column = {n.column: n for n in HEATMAP_NUMBERS}
    judged = by_column[JUDGED_ON]
    rows = [PatternRow(judged.title, judged.column, judged.unit, answers=True)]
    for column in PATTERN_STANDARD_COLUMNS:
        n = by_column[column]
        rows.append(PatternRow(n.title, n.column, n.unit))
    return rows


def column_seed_values(rows: Sequence[dict], column: str) -> dict[int, float]:
    """Return one number per seed from a results column.

    Args:
        rows: One scheduler's rows for a cell.
        column: The ``runs.csv`` column.

    Returns:
        Seed to value.
    """
    return {int(r["seed"]): float(r[column]) for r in rows}


def uneven_cells(rows: Sequence[dict]) -> list[str]:
    """Cells whose schedulers do not all share the same seed set.

    ``paired_diff`` compares only shared seeds, so an unequal cell still produces a number --
    it is just a weaker one than its neighbours, computed over fewer pairs, and nothing in the
    tables says so. A cell can end up uneven because a worker raised and its row was dropped.
    Every cell is checked against every scheduler that appears anywhere in ``rows``, so a
    scheduler with no rows at all in a cell is flagged too (with a count of 0) rather than
    silently left out of that cell's tie set.

    Args:
        rows: ``runs.csv`` rows.

    Returns:
        One human-readable line per uneven cell, naming each scheduler's seed count; empty
        when every cell is even.
    """
    # Collect the seed set each scheduler ran, per (preset, building, variant).
    seeds: dict[tuple, dict[str, set[int]]] = {}
    for r in rows:
        key = (r["preset"], r["building"], r["variant"])
        seeds.setdefault(key, {}).setdefault(r["scheduler"], set()).add(int(r["seed"]))
    # Every scheduler seen anywhere is expected in every cell; a missing one has no seeds.
    expected = sorted({r["scheduler"] for r in rows})
    uneven = []
    # A cell is uneven when any scheduler's seed set differs from the first one's.
    for key, by_scheduler in sorted(seeds.items()):
        sets = [by_scheduler.get(s, set()) for s in expected]
        if any(s != sets[0] for s in sets[1:]):
            counts = ", ".join(f"{s}={len(v)}" for s, v in zip(expected, sets, strict=True))
            uneven.append(f"{'/'.join(key)}: unequal seed sets ({counts})")
    return uneven


def leaders(by_seed: dict[str, dict[int, float]]) -> list[str]:
    """Every scheduler the best one is not separated from, on the paired comparison.

    Each scheduler ran the identical request file at a given seed, so the honest test is
    the per-seed difference, not the overlap of two marginal spreads. A challenger stays in
    the tie set until the paired difference clears the two-sided 95% t-critical value for
    its own degrees of freedom (`Paired.separated`'s default): 2.262 at the ten seeds every
    cell runs.
    The leader is picked after seeing the data and tested against every challenger at 95%
    with no multiple-comparison correction, so this leans slightly toward calling a
    separation.

    The leader is first; the rest follow in ``by_seed``'s order (``SCHEDULERS`` order when
    built by ``cell_rows``). A column headed "winner" that listed them alphabetically read
    as if the first name had won.

    Args:
        by_seed: Scheduler name to its per-seed values, as ``seed_values`` returns.

    Returns:
        The tie set, leader first; empty when no scheduler has any value.
    """
    # Schedulers with no values at all cannot lead or be compared, so leave them out.
    scored = {s: v for s, v in by_seed.items() if v}
    if not scored:
        return []
    # The leader is the best mean across seeds; everyone else is tested against it.
    best = min(scored, key=lambda s: mean(scored[s].values()))
    tied = [best]
    for name, values in scored.items():
        if name == best:
            continue
        try:
            difference = paired_diff(scored[best], values)
        # Too few shared seeds to separate anything, so the challenger stays tied.
        except ValueError:
            tied.append(name)
            continue
        if not difference.separated():
            tied.append(name)
    return [best] + [s for s in by_seed if s in tied and s != best]


def preset_table(rows: Sequence[dict], preset: Preset, variant: str | None = None) -> list[str]:
    """Return one preset's table: buildings down, schedulers across, the judged number per cell.

    Args:
        rows: ``runs.csv`` rows.
        preset: The preset to tabulate.
        variant: The experiment's variant; ``None`` = big-bang-0's.

    Returns:
        Markdown table lines. Each cell is the mean with the min-max spread as a subscript,
        and the last column is the tie set from ``leaders``. Buildings with no data are
        left out, so a table of only its two header lines means there was nothing to show.
    """
    grouped = group(rows)
    lines = header("Building", *SCHEDULERS, "winner")
    for building in BUILDINGS:
        rows_by_scheduler = cell_rows(grouped, preset, building, variant)
        cells = {s: cell(rs) for s, rs in rows_by_scheduler.items()}
        by_seed = {s: seed_values(rs) for s, rs in rows_by_scheduler.items()}
        # Skip a building this preset was never run on.
        if not any(cells.values()):
            continue
        # "-" marks a scheduler with no data in this cell.
        rendered = [
            "-" if c is None else f"{c[0]:.1f} <sub>{c[1]:.0f}–{c[2]:.0f}</sub>"
            for c in cells.values()
        ]
        won = leaders(by_seed)
        lines.append(f"| {building.name} | {' | '.join(rendered)} | {', '.join(won) or '-'} |")
    return lines


def ranking_table(rows: Sequence[dict], variant: str | None = None) -> list[str]:
    """Winner per preset and building on the judged number, bolding a tie set unlike B1's.

    The mark is on the *set*, not on the leader: a cell bolds when its tie set is not
    exactly B1's, which in this data almost always means B1's set grew rather than changed
    hands. The legend says so, because "differs from B1" reads as a ranking flip and almost
    none of these are.

    Args:
        rows: ``runs.csv`` rows.
        variant: The experiment's variant; ``None`` = big-bang-0's.

    Returns:
        Markdown table lines: one row per preset, one column per building.
    """
    grouped = group(rows)
    lines = header("Preset", *(b.name for b in BUILDINGS))
    for preset in PRESETS:
        winners = []
        for building in BUILDINGS:
            rows_by_scheduler = cell_rows(grouped, preset, building, variant)
            by_seed = {s: seed_values(rs) for s, rs in rows_by_scheduler.items()}
            winners.append(leaders(by_seed))
        # Compare every building's tie set with B1's (winners[0], the reference building).
        marked = [
            "-" if not w else (
                ", ".join(w) if set(w) == set(winners[0]) else f"**{', '.join(w)}**"
            )
            for w in winners
        ]
        lines.append(f"| {preset.name} | {' | '.join(marked)} |")
    return lines


def express_table(rows: Sequence[dict]) -> list[str]:
    """Every scheduler at stop time 0 and 3 on preset 8, where ``express`` should earn its keep.

    Every other table here reports the mean across seeds, so a bare "max total" column
    holding ``max`` over seeds -- the worst seed's own per-run max -- reads as a mean and is
    not one. Both are printed and both are named.

    Args:
        rows: ``runs.csv`` rows.

    Returns:
        Markdown table lines: one row per variant x scheduler that has data.
    """
    # Preset 8 is local-plus-express, the only preset with a 3-tick variant (on B1 only).
    preset = BY_NUMBER[8]
    grouped = group(rows)
    lines = header("Variant", "Scheduler", "mean total", "p95 total (mean)",
                   "max total (mean)", "max total (worst seed)")
    for variant in STOP_TIME_PAIR:
        for scheduler in SCHEDULERS:
            cells = grouped.get(
                (preset.name, REFERENCE_BUILDING.name, variant, scheduler), []
            )
            if not cells:
                continue
            means = [float(r["total_mean"]) for r in cells]
            p95s = [float(r["total_p95"]) for r in cells]
            maxes = [int(r["total_max"]) for r in cells]
            lines.append(
                f"| {variant} | {scheduler} | {mean(means):.1f} | {mean(p95s):.1f} | "
                f"{mean(maxes):.1f} | {max(maxes)} |"
            )
    return lines


def cost_table(rows: Sequence[dict]) -> list[str]:
    """Return CPU seconds per run by scheduler. Real-time dispatch has to be affordable.

    Args:
        rows: ``runs.csv`` rows.

    Returns:
        Markdown table lines: each scheduler's mean and worst CPU seconds per run.
    """
    by_scheduler: dict[str, list[float]] = {}
    for r in rows:
        by_scheduler.setdefault(r["scheduler"], []).append(float(r["seconds"]))
    lines = header("Scheduler", "mean CPU seconds", "worst cell")
    for scheduler in SCHEDULERS:
        seconds = by_scheduler.get(scheduler, [])
        if seconds:
            lines.append(f"| {scheduler} | {mean(seconds):.2f} | {max(seconds):.1f} |")
    return lines


def fairness_rows(path: Path = FAIRNESS) -> list[dict]:
    """Read the per-seed exponent sweep, as written by ``elevator-trials fairness``.

    Args:
        path: The fairness CSV to read.

    Returns:
        One row per (power, seed), values as strings.

    Raises:
        FileNotFoundError: the fairness sweep has not been run.
    """
    return read_rows(path)


def fairness_table(rows: Sequence[dict]) -> list[str]:
    """Return the mean across seeds per exponent, with the seed count so precision is legible.

    Args:
        rows: ``exponent_sweep.csv`` rows.

    Returns:
        Markdown table lines, one row per exponent ``p`` in ascending order.
    """
    # Gather each exponent's seeds together.
    by_power: dict[float, list[dict]] = {}
    for r in rows:
        by_power.setdefault(float(r["power"]), []).append(r)
    lines = header("p", "mean total", "p95 total", "max total", "seeds")
    for power, group in sorted(by_power.items()):
        lines.append(
            f"| {power} | {mean(float(r['total_mean']) for r in group):.1f} | "
            f"{mean(float(r['total_p95']) for r in group):.1f} | "
            f"{mean(float(r['total_max']) for r in group):.1f} | {len(group)} |"
        )
    return lines


NO_DIFFERENCE = "No real difference"
FASTER = "Fair version faster on average"
SLOWER = "Fair version slower on average"
SHORTER_TAIL = "Fair version cut the long waits"
LONGER_TAIL = "Fair version made the long waits longer"
TRADED = "Traded: cut the long waits but slower on average"
OUTCOMES = (NO_DIFFERENCE, FASTER, SLOWER, SHORTER_TAIL, LONGER_TAIL, TRADED)
"""Every ``TradeRow.outcome``, in the order the figure lists them: no change first, the
trade the experiment looks for last."""


@dataclass(frozen=True)
class TradeRow:
    """One cell of the fairness–efficiency trade-off: ETA-cost fair's change from ETA-cost.

    Changes are the mean per-seed paired difference, fair minus plain, as a percentage of
    plain ETA-cost's mean, so cells with very different trip lengths share one scale. Scaling
    by a constant leaves the paired test unchanged. Positive means fair is slower.
    """

    preset: str
    """Preset name, e.g. ``"evening-rush"``."""
    building: str
    """Building name, e.g. ``"B4"``."""
    variant: str
    """Variant name: ``"plain"``, or ``"stop3"`` for the stop-time pair's 3-tick arm."""
    average: float
    """Change in average trip time (``total_mean``), percent."""
    average_width: float
    """Half-width of the 95% interval on ``average``, percent."""
    tail: float
    """Change in 95-in-100 trip time (``total_p95``), percent."""
    tail_width: float
    """Half-width of the 95% interval on ``tail``, percent."""
    average_verdict: str
    """``"better"``, ``"worse"`` or ``"same"`` (not separated) for fair on the average."""
    tail_verdict: str
    """``"better"``, ``"worse"`` or ``"same"`` for fair on the 95-in-100."""
    identical: bool
    """True when both numbers are equal on every seed: the exponent changed nothing."""

    @property
    def label(self) -> str:
        """Short cell name for a chart label, e.g. ``"evening rush B4"``."""
        # Free stops (big-bang-0) need no suffix; the other variants name their stop cost.
        stop = f" stop {STOP_TIMES[self.variant]}" if self.variant != PLAIN.name else ""
        return f"{self.preset.replace('-', ' ')} {self.building}{stop}"

    @property
    def separated(self) -> bool:
        """True when either number is separated, in either direction."""
        return self.average_verdict != "same" or self.tail_verdict != "same"

    @property
    def trade(self) -> bool:
        """True for a real trade: a separated better tail with a separated worse average."""
        return self.tail_verdict == "better" and self.average_verdict == "worse"

    @property
    def outcome(self) -> str:
        """The cell's one plain-language outcome, from ``OUTCOMES``.

        Checked in order, so a cell lands in exactly one: no separated change (identical
        runs included), then the trade, then the tail, then the average.
        """
        if not self.separated:
            return NO_DIFFERENCE
        if self.trade:
            return TRADED
        if self.tail_verdict != "same":
            return SHORTER_TAIL if self.tail_verdict == "better" else LONGER_TAIL
        return FASTER if self.average_verdict == "better" else SLOWER


def fairness_tradeoff(grouped: dict[tuple, list[dict]]) -> list[TradeRow]:
    """Compare ``eta-cost-fair`` with ``eta-cost`` in every cell both ran.

    The one scheduler pair that differs only in the fairness exponent, compared per seed on
    average trip (efficiency) and 95-in-100 trip (fairness). The figure and the report table
    both read this, so they cannot disagree.

    Args:
        grouped: Rows grouped by cell, as ``group`` returns them.

    Returns:
        One row per ``(preset, building, variant)`` in sorted order; cells where the two
        arms share fewer than two seeds are left out, since they cannot be tested.
    """
    cells = sorted({key[:3] for key in grouped
                    if key[3] == "eta-cost" and (*key[:3], "eta-cost-fair") in grouped})
    out = []
    for cell in cells:
        plain, fair = grouped[(*cell, "eta-cost")], grouped[(*cell, "eta-cost-fair")]
        numbers = []
        for column in ("total_mean", "total_p95"):
            base = column_seed_values(plain, column)
            other = column_seed_values(fair, column)
            try:
                # Positive means fair is better (lower), as paired_diff's first arm.
                diff = paired_diff(other, base)
            except ValueError:
                break
            width = diff.half_width()
            verdict = ("better" if diff.mean > 0 and diff.mean > width
                       else "worse" if -diff.mean > width else "same")
            # Percent of plain ETA-cost's mean; fair minus plain, so slower is positive.
            scale = 100 / mean(base[s] for s in other if s in base)
            numbers.append((-diff.mean * scale, width * scale, verdict, base == other))
        # A cell with too few shared seeds broke out above and is left out.
        if len(numbers) < 2:
            continue
        (average, average_width, average_verdict, same_average), \
            (tail, tail_width, tail_verdict, same_tail) = numbers
        out.append(TradeRow(*cell, average, average_width, tail, tail_width,
                            average_verdict, tail_verdict, same_average and same_tail))
    return out


def fairness_tradeoff_table(trades: Sequence[TradeRow]) -> list[str]:
    """Return the trade-off as a summary line and one table row per cell.

    Args:
        trades: The cells, as ``fairness_tradeoff`` returns them.

    Returns:
        Markdown lines: a count summary, a blank line, then the table (changes in percent of
        ETA-cost's value, with the 95% half-width; verdicts for fair).
    """
    lines = [
        f"{len(trades)} cells: {sum(t.trade for t in trades)} trade (tail separated and better "
        f"with average separated and worse), {sum(t.separated for t in trades)} separated on "
        f"either number, {sum(t.identical for t in trades)} identical on every seed.",
        "",
        *header("Cell", "average change %", "average", "95-in-100 change %", "95-in-100"),
    ]
    for t in trades:
        lines.append(
            f"| {t.label} | {t.average:+.2f} ± {t.average_width:.2f} | {t.average_verdict} | "
            f"{t.tail:+.2f} ± {t.tail_width:.2f} | {t.tail_verdict} |")
    return lines


# -- assembly --------------------------------------------------------------------------

STOP_TIMES = {v.name: v.stop_time for v in (PLAIN, STOP1, STOP3)}
"""Stop cost in ticks for each variant name, for headings."""


def experiment_section(rows: Sequence[dict], experiment: str, variant: str) -> list[str]:
    """Return one big-bang experiment's part of the report: ranking, compute cost, presets.

    Args:
        rows: ``runs.csv`` rows, every experiment's.
        experiment: The experiment's name, e.g. ``"big-bang-1"``.
        variant: The variant its cells carry, e.g. ``"stop1"``.

    Returns:
        Markdown lines: a heading, the ranking-stability table, the compute-cost table over
        this experiment's runs only, and one table per preset that has data.
    """
    stop = STOP_TIMES[variant]
    own = [r for r in rows if r["variant"] == variant]
    out = [
        f"## {experiment}: every preset on every building, stops costing {stop} "
        f"tick{'' if stop == 1 else 's'}",
        "",
        # Cross-building roll-up: which schedulers tie for the lead in each cell.
        "### Ranking stability",
        "",
        "The winner per preset and building. Where several schedulers are listed, none of "
        "their paired differences from the leader clears its own t-critical value, so "
        "there is no winner the data separates from the rest. **Bold** marks a cell whose "
        "tie set differs from the reference building B1's. That is usually not a ranking "
        "flip but challengers added or dropped; every building runs ten seeds, so the bar "
        "is the same t = 2.262 everywhere. `docs/write-up/5-findings.md` names the cells where "
        "the tie set changed hands.",
        "",
        *ranking_table(rows, variant),
        "",
        # CPU seconds per run, by scheduler, over this experiment's runs only.
        "### Compute cost",
        "",
        "Not an outcome, but it decides whether a scheduler is usable in real time.",
        "",
        *cost_table(own),
        "",
        "### Per preset",
        "",
    ]
    for preset in PRESETS:
        table = preset_table(rows, preset, variant)
        # Two lines is the header and separator alone: no building had data, so skip it.
        if len(table) <= 2:
            continue
        # Heading, hypothesis, and the demand with the arrival rate it gives on B1.
        out += [
            f"#### {preset.number}. {preset.name}",
            "",
            f"*Hypothesis.* {preset.hypothesis}",
            "",
            f"Demand {preset.load} of the fleet's nominal throughput "
            f"({rate_for(preset, REFERENCE_BUILDING):.3f}/tick on {REFERENCE_BUILDING.name}).",
            "",
            *table,
            "",
        ]
    return out


def write_report(path: Path = REPORT) -> Path:
    """Write the markdown report from every experiment's results and, if present, the sweep.

    Sections, in order: an introduction on how to read the tables, a warning if any cell
    has uneven seed sets, one section per big-bang experiment (``experiment_section``), the
    stop-time pair, the fairness–efficiency trade-off, and the fairness sweep when
    ``exponent_sweep.csv`` exists.

    Args:
        path: Where to write the report; parent directories are created.

    Returns:
        ``path``, once written.

    Raises:
        FileNotFoundError: ``runs.csv`` does not exist yet.
    """
    rows = read_rows()
    uneven = uneven_cells(rows)
    # ``out`` collects the report line by line; it is joined with newlines at the end.
    out = [
        # Title and the "how to read this" introduction.
        "# Scheduler comparison: results",
        "",
        "Generated by `elevator-trials report`. Every scheduler runs on the identical "
        "request file for a given seed, so a difference between columns is assignment and "
        "not the draw -- the comparison is paired, and the per-seed difference isolates the "
        "scheduler. That pairing is the strongest thing about this study, and it is what the "
        "winner column below actually uses. Cells show the mean across seeds with the "
        "<sub>min–max</sub> spread beneath as context only; it is **not** the tie-breaking "
        "statistic. A challenger is dropped from the winner column only once its paired "
        "difference from the leader clears the two-sided 95% t-critical value for its own "
        "degrees of freedom (2.262 at ten seeds), so two means whose printed spreads "
        "overlap can still be a clean, separated win. Every cell is judged on one number, "
        "the mean total time (request to drop-off, `total_mean`). Every run drains: "
        "overload stops adding riders at tick 1500 and then runs until everyone is "
        "delivered, and every statistic counts every rider in the run.",
        "",
        # Only when some cell has unequal seed sets: a blockquote naming each one.
        *([
            "> **Warning.** These cells do not have the same seeds in every arm, so their "
            "paired comparisons rest on fewer pairs than the rest of the table:",
            "",
            *(f"> - {line}" for line in uneven),
            "",
        ] if uneven else []),
    ]
    # One section per big-bang experiment; the two differ only in what a stop costs.
    for experiment, variant in EXPERIMENTS.items():
        out += experiment_section(rows, experiment, variant)
    # The stop-time pair is preset 8's own experiment, so it gets its own section.
    out += ["## Stop time and the express scheduler (preset 8 on B1)", "",
            "The one preset run at two stop times. `express` keeps one car (two on a large "
            "fleet) on lobby-to-top-band trips and gives those trips to no other car; at stop "
            "time 0 skipping floors saves nothing, so that split can only cost, and it can "
            "pay only once stops cost time.", "",
            *express_table(rows), ""]
    # The trade-off re-reads every experiment's runs: fair against plain ETA-cost per cell.
    out += ["## Fairness–efficiency trade-off", "",
            "`eta-cost-fair` against `eta-cost` in every cell both ran, paired per seed. "
            "A change is fair minus plain as a percentage of plain's value (positive = fair "
            "slower), with the half-width of its 95% interval; the verdict is the paired "
            "test's.", "",
            *fairness_tradeoff_table(fairness_tradeoff(group(rows))), ""]
    # The fairness sweep is a follow-up command, so its section is optional.
    if FAIRNESS.exists():
        out += ["## Fairness sweep (follow-up)", "",
                "The cost exponent swept with the environment held at one cell "
                f"({BY_NUMBER[FAIRNESS_PRESET].name} on B1) -- a single cell and not an "
                "experiment, run as a follow-up. One run per seed is recorded in "
                "`exponent_sweep.csv`, so this comparison can be paired per seed like every "
                "other in this report. Raising `p` should trade mean for max.", "",
                *fairness_table(fairness_rows()), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out))
    return path


TIED = "#2a78d6"
"""Colour of schedulers tied for best on a row's number (one emphasis hue)."""
DOT_STRIP_LEGEND = (
    "dot = one list; black tick = mean; blue = tied for best on that row's number "
    "(paired test), grey = separated and worse; log scale shared across each row")
"""Second title line of every dot-strip figure, saying how to read it."""
SEPARATED = "#8a8880"
"""Colour of schedulers the paired test separates from the leader, as worse: a grey dark
enough to clear 3:1 contrast on white, light enough to stay recessive beside ``TIED``."""


def plain_number(value: float, _position: object = None) -> str:
    """Return a log-axis tick label as a plain number rather than a power of ten.

    Args:
        value: The tick value.
        _position: Tick position; required by matplotlib's formatter signature, unused.

    Returns:
        ``"30"``, ``"300"`` or ``"0.5"``, say.
    """
    return f"{value:g}"


def write_pattern_pages(grouped: dict[tuple, list[dict]], plt, path: Path,
                        variant: str | None = None, experiment: str = "big-bang-0",
                        ) -> list[Path]:
    """Draw one page per pattern: every list's value, per scheduler, building and number.

    Rows are the numbers from ``pattern_rows`` (the judged one first); columns are buildings in
    one experiment. Each panel is a dot strip: one dot per list, a tick at
    the mean, schedulers along the bottom and values going up. Schedulers the paired test
    cannot separate from the best on that row's number are blue; the rest grey. Each row
    shares one log scale.

    Args:
        grouped: Rows grouped by cell, as ``group`` returns them.
        plt: ``matplotlib.pyplot``, already imported by ``write_figures``.
        path: Directory to write the PNGs into; created if missing.
        variant: The experiment's variant; ``None`` = big-bang-0's.
        experiment: The experiment's name, printed in each page's title.

    Returns:
        The paths of the PNGs written, in ``PRESETS`` order.
    """
    path.mkdir(parents=True, exist_ok=True)
    made = []
    for preset in PRESETS:
        spec = pattern_rows(preset)
        fig, axes = plt.subplots(len(spec), len(BUILDINGS), squeeze=False,
                                 figsize=(2.3 * len(BUILDINGS) + 1.6, 2.2 * len(spec) + 2.0))
        for i, row in enumerate(spec):
            # Every seed's value for every panel in the row, so the row can share one scale.
            panels = {}
            for b in BUILDINGS:
                by_seed = {s: column_seed_values(rs, row.column)
                           for s, rs in cell_rows(grouped, preset, b, variant).items()}
                panels[b.name] = (by_seed, leaders(by_seed))
            values = [v for by_seed, _ in panels.values()
                      for seeds in by_seed.values() for v in seeds.values() if v > 0]
            for j, b in enumerate(BUILDINGS):
                ax = axes[i][j]
                by_seed, tied = panels[b.name]
                draw_dot_strip(ax, by_seed, tied, values=values, first_column=j == 0,
                               last_row=i == len(spec) - 1)
                if i == 0:
                    ax.set_title(f"{b.name}: {b.floors} floors, {b.cars} cars, cap {b.capacity}",
                                 fontsize=8)
            label_row(axes[i][0], row)
        title = preset.name.replace("-", " ").capitalize()
        fig.suptitle(f"{experiment} · {title}: every list, by scheduler and building\n"
                     f"{DOT_STRIP_LEGEND}", fontsize=9)
        fig.tight_layout()
        made.append(path / f"{preset.name}.png")
        fig.savefig(made[-1], dpi=120)
        plt.close(fig)
    return made


def draw_dot_strip(ax, by_seed: dict[str, dict[int, float]], tied: Sequence[str], *,
                   values: Sequence[float], first_column: bool, last_row: bool) -> None:
    """Draw one dot-strip panel: a dot per list and a tick at the mean, per scheduler.

    Shared by the pattern pages and the stop-time pair so every panel reads the same way.

    Args:
        ax: The matplotlib axes to draw into.
        by_seed: Scheduler name to its seed-to-value map for this panel's number.
        tied: Schedulers tied for best on this panel's number; drawn in ``TIED``.
        values: Every positive value across the panels sharing this row, to fix one scale.
        first_column: True to label the y ticks; the rest of the row shares the scale.
        last_row: True to print the scheduler names along the bottom.
    """
    from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

    for k, scheduler in enumerate(SCHEDULERS):
        seeds = [v for v in by_seed.get(scheduler, {}).values() if v > 0]
        if not seeds:
            continue
        colour = TIED if scheduler in tied else SEPARATED
        # Spread a cell's dots over a thin band (by seed order) so equal values
        # stay countable instead of printing on top of each other.
        offsets = [((n % 5) - 2) * 0.07 for n in range(len(seeds))]
        ax.scatter([k + o for o in offsets], seeds, s=14, color=colour,
                   alpha=0.8, linewidths=0, zorder=3)
        centre = mean(seeds)
        ax.plot([k - 0.32, k + 0.32], [centre, centre], color="#0b0b0b",
                linewidth=1.5, zorder=4)
    ax.set_yscale("log")
    if values:
        ax.set_ylim(min(values) / 1.15, max(values) * 1.15)
    # Plain-number ticks at 1, 2 and 5 of each decade, labelled on the first
    # column only since the whole row shares the scale.
    ax.yaxis.set_major_locator(LogLocator(subs=(1.0, 2.0, 5.0)))
    ax.yaxis.set_major_formatter(
        FuncFormatter(plain_number) if first_column else NullFormatter())
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.tick_params(axis="y", labelsize=7)
    ax.set_xlim(-0.5, len(SCHEDULERS) - 0.5)
    # Scheduler names on the bottom row only, rotated so the full names fit.
    ax.set_xticks(range(len(SCHEDULERS)),
                  SCHEDULERS if last_row else [""] * len(SCHEDULERS), fontsize=7,
                  rotation=45 if last_row else 0, ha="right" if last_row else "center")
    ax.grid(axis="y", color="#e1e0d9", linewidth=0.6, zorder=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def label_row(ax, row: PatternRow) -> None:
    """Put a row's title, unit and direction on the y axis of its first panel.

    Args:
        ax: The row's first axes.
        row: The number the row shows.
    """
    note = "\n(the judged number)" if row.answers else ""
    ax.set_ylabel(f"{row.label}\n{row.unit}, lower is better{note}", fontsize=8)


def write_stop_time_pair(grouped: dict[tuple, list[dict]], plt, path: Path = STOP_TIME_DIR
                         ) -> Path:
    """Draw the stop-time pair: preset 8 on B1 at stop time 0 and 3, every list per scheduler.

    Same rows and panels as a pattern page, but the columns are the two stop costs on the
    one building rather than the seven buildings, so the express question is read across:
    does ``express`` move from grey to blue once stops cost time?

    Args:
        grouped: Rows grouped by cell, as ``group`` returns them.
        plt: ``matplotlib.pyplot``, already imported by ``write_figures``.
        path: Directory to write the PNG into; created if missing.

    Returns:
        The path of the PNG written.
    """
    path.mkdir(parents=True, exist_ok=True)
    preset = BY_NUMBER[8]
    building = REFERENCE_BUILDING
    # Only the pair's two arms: this cell also carries big-bang-1's 1-tick run.
    variants = [v for v in preset.variants(building) if v.name in STOP_TIME_PAIR]
    spec = pattern_rows(preset)
    fig, axes = plt.subplots(len(spec), len(variants), squeeze=False,
                             figsize=(2.3 * len(variants) + 1.6, 2.2 * len(spec) + 2.0))
    for i, row in enumerate(spec):
        # Every seed's value for both arms, so the row shares one scale across the pair.
        panels = {}
        for variant in variants:
            by_seed = {
                s: column_seed_values(
                    grouped.get((preset.name, building.name, variant.name, s), []), row.column)
                for s in SCHEDULERS
            }
            panels[variant.name] = (by_seed, leaders(by_seed))
        values = [v for by_seed, _ in panels.values()
                  for seeds in by_seed.values() for v in seeds.values() if v > 0]
        for j, variant in enumerate(variants):
            ax = axes[i][j]
            by_seed, tied = panels[variant.name]
            draw_dot_strip(ax, by_seed, tied, values=values, first_column=j == 0,
                           last_row=i == len(spec) - 1)
            if i == 0:
                ax.set_title(f"Stop cost {variant.stop_time} ticks", fontsize=8)
        label_row(axes[i][0], row)
    # Two columns make a narrow page, so the legend is broken into three lines.
    legend = DOT_STRIP_LEGEND.replace("mean; ", "mean;\n").replace("worse; ", "worse;\n")
    fig.suptitle(
        f"Local + express on {building.name} at two stop costs: every list, by scheduler\n"
        f"{legend}", fontsize=9)
    fig.tight_layout()
    out = path / f"{preset.name}-{building.name.lower()}.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def write_fairness_tradeoff(grouped: dict[tuple, list[dict]], plt,
                            path: Path = FAIRNESS_TRADE_DIR) -> Path:
    """Draw the trade-off as a bar chart: how many cells had each outcome.

    One bar per outcome in ``OUTCOMES``, in plain words, with its count printed at the end.
    Empty outcomes are left out except the trade, which is the question the experiment asks,
    so its zero stays visible. The exact per-cell numbers are in ``report.md``.

    Args:
        grouped: Rows grouped by cell, as ``group`` returns them.
        plt: ``matplotlib.pyplot``, already imported by ``write_figures``.
        path: Directory to write the PNG into; created if missing.

    Returns:
        The path of the PNG written.
    """
    path.mkdir(parents=True, exist_ok=True)
    trades = fairness_tradeoff(grouped)
    counts = {outcome: sum(t.outcome == outcome for t in trades) for outcome in OUTCOMES}
    shown = [o for o in OUTCOMES if counts[o] or o == TRADED]
    fig, ax = plt.subplots(figsize=(8.0, 0.55 * len(shown) + 2.4))
    # First outcome at the top, so the chart reads down like a list.
    rows = range(len(shown))[::-1]
    ax.barh(list(rows), [counts[o] for o in shown], height=0.6, color=TIED,
            edgecolor="white", linewidth=2)
    top = max(counts.values(), default=0) or 1
    for y, outcome in zip(rows, shown, strict=True):
        # Counts sit just past the bar's end, so a zero still gets its label.
        ax.text(counts[outcome] + top * 0.01, y, str(counts[outcome]), va="center",
                fontsize=10, fontweight="bold", color="#0b0b0b")
    ax.set_yticks(list(rows), shown, fontsize=9)
    ax.set_xlim(0, top * 1.1)
    ax.set_xlabel("Number of cases", fontsize=9)
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(axis="x", color="#e1e0d9", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    # A figure-level title, left-aligned to the figure edge: the long bar labels take the
    # left of the canvas, and an axes title there would run off the right.
    fig.suptitle(
        "A \"fairer\" elevator scheduler almost never changed the result",
        fontsize=11, fontweight="bold", x=0.02, ha="left")
    # The comparison, the test and the terms, so the figure reads without the write-up.
    fig.text(0.02, 0.915, "\n".join((
        "Compared: the standard scheduler, which sends each new rider to the car that adds the "
        "least total trip time,",
        "and a fair version that does the same but counts long trips extra, to avoid leaving "
        "anyone waiting very long.",
        f"Both were run in {len(trades)} cases (building × traffic pattern), on the same 10 "
        "sets of passenger requests.",
        "\"Long waits\" = the slowest 5 trips in 100.",
        "\"No real difference\" = identical results, or a gap too small to tell apart from "
        "chance.")),
        fontsize=8.5, va="top", ha="left", color="#3d3d3a", linespacing=1.4)
    # Leave the top of the canvas to the title and the five explanation lines.
    fig.tight_layout(rect=(0, 0, 1, 0.77))
    out = path / "trade-off.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def write_overview(grouped: dict[tuple, list[dict]], plt, path: Path,
                   variant: str | None = None, experiment: str = "big-bang-0") -> Path:
    """Draw the one-glance overview: who is tied for best, per pattern and building.

    The same tie sets as ``ranking_table``, on the judged number. The leader is bold; a cell
    whose set differs from B1's is shaded.

    Args:
        grouped: Rows grouped by cell, as ``group`` returns them.
        plt: ``matplotlib.pyplot``, already imported by ``write_figures``.
        path: Directory to write the PNG into; created if missing.
        variant: The experiment's variant; ``None`` = big-bang-0's.
        experiment: The experiment's name, printed in the title.

    Returns:
        The path of the PNG written.
    """
    path.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(2.0 * len(BUILDINGS) + 2.6, 1.25 * len(PRESETS) + 1.2))
    ax.set_xlim(-1, len(BUILDINGS))
    ax.set_ylim(len(PRESETS) - 0.5, -0.9)
    ax.axis("off")
    for j, b in enumerate(BUILDINGS):
        ax.text(j, -0.75, f"{b.name}\n{b.floors} fl, {b.cars} cars, cap {b.capacity}",
                ha="center", va="center", fontsize=8)
    for i, preset in enumerate(PRESETS):
        ax.text(-0.55, i, preset.name, ha="right", va="center", fontsize=8)
        sets = [leaders({s: seed_values(rs)
                         for s, rs in cell_rows(grouped, preset, b, variant).items()})
                for b in BUILDINGS]
        for j, tied in enumerate(sets):
            # Shade a cell whose tie set is not B1's (sets[0]), as the ranking table bolds it.
            if j and set(tied) != set(sets[0]):
                ax.add_patch(plt.Rectangle((j - 0.48, i - 0.46), 0.96, 0.92,
                                           color="#eef4fc", zorder=0))
            for k, name in enumerate(tied):
                ax.text(j, i - 0.36 + k * 0.15, name, ha="center", va="center", fontsize=7,
                        fontweight="bold" if k == 0 else "normal")
    ax.set_title(
        f"{experiment}: which scheduler works, per pattern and building\n"
        "schedulers tied for best on mean total time (paired test); "
        "leader in bold; shaded = tie set differs from B1's", fontsize=9)
    fig.tight_layout()
    out = path / "best-schedulers.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def write_figures(path: Path = FIGURES, results: Path = RESULTS) -> list[Path]:
    """Render the figures, one subdirectory per experiment. Needs matplotlib (dev group).

    ``big-bang-0/`` and ``big-bang-1/`` each hold one big-bang experiment: one page per
    traffic pattern (``write_pattern_pages``, in ``by-traffic/``) and the overview of who is
    tied for best (``write_overview``, ``best-schedulers.png``). ``stop-time-pair/`` holds the
    stop-time pair (``write_stop_time_pair``) and ``fairness-efficiency/`` the trade-off
    (``write_fairness_tradeoff``). The earlier B1 bar chart, fairness curve,
    per-window series plots and heatmaps were retired; ``docs/write-up/5-findings.md`` says
    how to revive them.

    Args:
        path: Directory to write the PNGs into; created if missing.
        results: The ``runs.csv`` to draw from.

    Returns:
        The paths of the PNGs written, in the order they were drawn.

    Raises:
        RuntimeError: matplotlib is not installed.
        FileNotFoundError: ``runs.csv`` does not exist yet.
    """
    # Import lazily so the rest of the harness works without matplotlib; "Agg" is the
    # file-only backend, so no display is needed.
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise RuntimeError(
            "figures need matplotlib: `uv sync` installs it from the dev group, "
            "or re-run with --no-figures"
        ) from None

    rows = read_rows(results)
    path.mkdir(parents=True, exist_ok=True)
    grouped = group(rows)
    made: list[Path] = []
    # Each big-bang experiment: one page per traffic pattern, then the overview beside them.
    for experiment, variant in EXPERIMENTS.items():
        folder = path / experiment
        made += write_pattern_pages(grouped, plt, folder / BY_TRAFFIC, variant, experiment)
        made.append(write_overview(grouped, plt, folder, variant, experiment))
    # The stop-time pair in its own directory.
    made.append(write_stop_time_pair(grouped, plt, path / STOP_TIME_DIR.name))
    # The fairness–efficiency trade-off, re-reading the same rows.
    made.append(write_fairness_tradeoff(grouped, plt, path / FAIRNESS_TRADE_DIR.name))
    return made
