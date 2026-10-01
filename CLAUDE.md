# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Take-home: a discrete-time elevator simulation (`elevator-sim`) plus an evaluation harness
(`elevator-trials`) that compares seven schedulers over every traffic pattern and building,
once with free stops (big-bang-0) and once with 1-tick stops (big-bang-1). The deliverables are
the code, `README.md`, and `docs/write-up/`.

## Commands

Requires [uv](https://docs.astral.sh/uv/); Python 3.12 is pinned in `.python-version`.
All paths in the harness are relative, so run everything from the repo root.

```bash
uv sync
uv run pytest                              # whole suite (10s per-test timeout)
uv run pytest tests/test_simulation.py -k boarding   # one file / one test
uv run ruff check .                        # line-length 100, rules E,F,I,UP,B,SIM,D (src only)
uv run elevator-sim data/sample_requests.csv --elevators 2 --floors 60 --capacity 8
```

`--scheduler`, `--stop-time`, `--max-ticks` and the two `--*-out` log paths are the rest of
the simulator CLI; `README.md` has the table. There is no fleet-level express setting:
`express` is a scheduler (`--scheduler express`, needs two or more cars).

Regenerating results, in this order (`report` reads `run`'s files):

```bash
uv run elevator-trials run         # every experiment -> docs/results/data/runs.csv (resumable)
uv run elevator-trials report      # docs/results/report.md + figures/ (needs dev matplotlib)
uv run elevator-trials gen <preset> --out requests.csv   # inspect one request file
uv run elevator-trials sweep       # follow-up, not in the reported results: demand sweep
uv run elevator-trials fairness    # follow-up, not in the reported results: exponent sweep
```

The arrival rate is fixed by rule, not measured: `0.75 × cars × capacity / (2 × floors)`
riders per tick for every preset (overload 1.5×), see `presets.rate_for`. `run` skips cells
already present in both `runs.csv` and `runs-windows.csv`, so it resumes, and re-runs a cell
missing only its series rows to backfill them; `sweep` does not resume. Both take `--jobs`.

## Results: regenerated 2026-09-30, and how to regenerate again

There are three experiments, told apart by the `variant` column of `runs.csv`: **big-bang-0**
(`plain`, free stops: every preset × building × scheduler × seed, 3,920 runs), **big-bang-1**
(`stop1`, the same at 1-tick stops, 3,920 runs) and the **stop-time pair** (B1's
local-plus-express cell at `plain` vs `stop3`, adding 70 runs): 7,910 in all.
`presets.EXPERIMENTS` maps each big-bang name to its variant. The fairness–efficiency
trade-off re-reads every cell where both ETA-cost arms ran (113).

`docs/results/` (runs.csv, runs-windows.csv, report.md, figures) was regenerated from
scratch on 2026-09-30, all 7,910 runs, after the last design changes (one judging metric
everywhere, ten seeds on every building, `express` owning its band, per-flow reporting removed,
overload draining after its arrivals stop, the controller's seat for a starved rider,
big-bang-1 added, and the 10% warm-up dropped so every statistic counts every rider). Every
number in `README.md`, `docs/write-up/5-findings.md` and `6-follow-ups.md` was recomputed from
those CSVs the same day.
The scripts that did the recomputation used `report.group`, `cell_rows`, `column_seed_values`,
`leaders` and `paired_diff`; nothing in the repo caches a number outside the CSVs and the prose.

If the experiments ever have to be re-run after a change that moves results, **delete
`docs/results/data/runs.csv` and `runs-windows.csv` first**: the runner's resume keys on
cell identity and would keep stale rows. Then `uv run elevator-trials run`, `report`, and
recompute every count in the four documents above and the Working conventions bullet below.

## Architecture

The hard line in this codebase is **engine versus scheduler**. Keep it.

`simulation.py` owns physics and constraints only — releasing requests at their request
time (never peeking ahead), alighting riders, validating and applying a plan, holding cars
for `stop_time`, moving one floor per tick, logging. It makes no scheduling decision. Its
tick order is documented at the top of that module and in
`docs/write-up/2-code-architecture.md`; changing it changes
every recorded result.

A `Scheduler` (`scheduler.py`) is asked once per tick via `step(state) -> {car_id:
CarAction}`, where `CarAction` is a target floor plus ids to board. `state` is a frozen
`SimulationState` built from frozen `ElevatorView`/`Rider` views — schedulers can never
reach the engine's mutable `Elevator`/`Passenger` objects in `models.py`. An invalid plan
raises `SchedulingError`; the whole plan is validated before any car is touched.

Schedulers are `BuildingConfig -> Scheduler` factories in one plain dict,
`elevator_sim.algorithms.BUILTINS`. There is no registration machinery: adding a key is all
there is to adding a scheduler, and `--scheduler`'s choices come from `available()`.

Inside `algorithms/`, the three layers are deliberately separate:
- `controller.py` — LOOK with direction commitment, over a small mutable `Car`. `simulate()`
  replays the same rules forward, which is what ETAs are made of. Once a car's oldest pickup
  has waited `starve_limit(config)` ticks (6 round trips, `12 × floors`), the car boards others
  only while a seat stays free for it; that is what bounds every wait (objective 1), for all
  seven. The threshold is a pure function of the config so the live car (`dispatch.py`) and
  the policies' replays agree; pass it to every `from_view`.
- `policies.py` — assignment rules only (which car takes a new request). Pure functions of
  the snapshot; they never mutate their inputs. `Express` and `ZoneBased` are wrappers that
  narrow the candidate list and hand it to an inner policy; `top_band` / `express_floors`
  define the express band, and the trials import that rule from here.
- `dispatch.py` — `DestinationDispatch`, the only stateful piece (per-car assignments and
  committed heading), binding a request to one car at release and never reassigning.

All seven built-ins share the controller and differ only in policy, so controller changes
move every scheduler's numbers at once. The engine has no served floors: `BuildingConfig`
is cars, floors, capacity and stop time, and `validate_requests` checks only ids and floor
range. Express is the `express` scheduler's own split of the fleet: one car in four (at
least one) owns every lobby-to-top-third trip, the rest own everything else.

`trials/` is a separate world: it imports the simulator, and nothing in `elevator_sim`
imports it. `presets.py` holds the eight traffic patterns and seven buildings (`B1` is the
reference; every building runs 10 seeds), `traffic.py` generates request files from a
seed,
`presets.rate_for` fixes the arrival rate by rule, `runner.py` walks the cells, `metrics.py` /
`paired.py` do the per-window max and the paired *t* comparison (no warm-up is trimmed: every
statistic counts every rider), `report.py` writes tables
and figures.

The runtime package has zero third-party dependencies; matplotlib, pytest and ruff are the
dev group. Keep `src/elevator_sim/` stdlib-only.

## Docs

- `README.md` — the deliverable front page: how to run the simulator and the trials, time
  spent, key assumptions and top improvements. Everything else it points to in
  `docs/write-up/` rather than repeating. Its `## Time spent` section is still `_TBD_`.
- `docs/results/` — generated output (`runs.csv`, `runs-windows.csv`, `report.md`, and
  `figures/` with one folder per experiment).
- `docs/write-up/` — `1-overview.md`, `2-code-architecture.md`, `3-algorithms.md`,
  `4-trial-design.md`, `5-findings.md`, `6-follow-ups.md`: the write-up in six parts, numbered
  in reading order,
  linked from `README.md`. `6-follow-ups.md` is the one canonical list of deferred
  experiments and improvements; other docs link to it rather than keeping their own. Rules
  for editing them:
  - Every number they quote must match `docs/results/*.csv`.
  - Each file stays under 500 words, except `1-overview.md` at 550, `3-algorithms.md` at 750,
    `4-trial-design.md` at 2,700, `5-findings.md` at 2,700, which carries the figures guide,
    and `6-follow-ups.md` at 1,200 (`wc -w docs/write-up/*.md`);
    trim elsewhere to make room.
  - Written for a first-year college reader, with no padding. Define jargon (Poisson, p95,
    paired t-test, ...) where it first appears.
  - Assumptions are labelled **explicit** (stated in the take-home prompt) or
    **implicit** (our choice). Prompt and simulator assumptions go in `1-overview.md`;
    experiment choices go in `4-trial-design.md`, where the reader meets them.
  - No opening note placing a file in the series; each ends with the previous / overview /
    next nav line. Hand-wrap at 100 columns.
  - Never label a doc or docstring by its audience ("plain-language", "non-technical",
    "In plain English", "for a first-year reader"). Write clearly; don't announce it.
  - `2-code-architecture.md` is the exception to the reader-level and word-cap rules above: a
    technical developer reference (a 10–50-word description per module, scheduler protocol,
    the runnable `BUILTINS` example, tick order), capped at 1,300 words. It still takes the
    nav line.

## Working conventions

- Every statistical claim in `README.md` and `docs/write-up/5-findings.md` is meant to be checkable
  against `docs/results/*.csv`. Several commits exist purely to correct overstated or mislabelled
  numbers — re-derive a figure from the CSVs before restating it, and if a claim was measured at one
  cell, say so rather than generalising it.
- A count of cells is ambiguous unless it names both the metric and the cell set, and two of
  them have been wrong in this repo before. Each big-bang experiment has **56 cells** (8
  presets × 7 buildings; B1's local-plus-express cell in big-bang-0 is the `plain` arm of the
  stop-time pair, and the `stop3` arm sits outside both). Unless stated, the counts below are
  **big-bang-0's**; big-bang-1's are in `5-findings.md` (e.g. `eta-cost-fair` better in 26,
  `express` leads 2 and is worse than `eta-cost` in 54, median gap 24.8% vs 5.3%). Every cell
  is judged on `total_mean`
  (`report.JUDGED_ON`); every run drains (overload stops *arrivals* at tick 1500 and runs
  until everyone is delivered), so nothing is censored and `unserved` is always 0. Over the
  56, `eta-cost-fair` has the better mean in **21**. `express` is a
  seventh arm and is **not** a baseline: every "against the baselines" figure excludes it.
  On the judged number it leads the tie set in **3 of 56** (overload on B2 and skewed
  two-way on B3, both alone; morning rush on B3, a four-way tie) and has the worse
  `total_mean` than `eta-cost` in **53 of 56**, so it is a specialist. Per-group (per-flow)
  reporting was removed on 2026-09-30: there is no `per_flow` column. The 32 B1 headline
  comparisons are `eta-cost` against each of the four baselines on `total_mean`; the 24
  against the original three have paired *t* 5.73 to 52.59, and of the eight against
  `nearest-car-eta` five are separated (t 3.10 to 12.59) and three are not. Any "against the
  best baseline" figure must say whether it means the original three baselines or all four
  -- `nearest-car-eta` was added later and moves them (median gap 24.3% vs 4.7% over the 56
  cells). Among the original five arms no baseline survives into any tie set; with
  `nearest-car-eta` one does in 19 cells, and it is ahead of `eta-cost` (separated, under a
  tick) on morning rush at B3, B4 and B7 only. All of these were computed at the fixed
  arrival rate (`presets.rate_for`); numbers from the earlier calibrated-rate runs are not
  comparable.
- Comparisons are paired per seed (every scheduler sees the identical request file) and read
  against a degrees-of-freedom-aware *t*-critical value, not overlapping min–max ranges.
- `ruff format` is **not** applied to this codebase (it would reformat most files); only
  `ruff check` is clean. Match the surrounding hand-wrapped style at 100 columns.
- Documentation is written for a junior engineer new to the code. In `src/`:
  - Every module, class, function, method and property (including private helpers and
    nested closures) has a Google-style docstring: a one-line summary ending in a period,
    then `Args:` (every parameter bar `self`/`cls`), `Returns:` (unless it returns
    `None`; not on properties) and `Raises:`. Types live in the hints, not the docstring;
    say units (ticks, 1-based floors) and what `None` means. Closures may be one line.
  - Dataclass fields keep their attribute docstrings; no duplicate `Attributes:` section.
  - Module and class docstrings still carry the design reasoning; define jargon (LOOK,
    ETA, paired test, nominal throughput, ...) in plain English where a module first uses it.
  - Any function body over 10 lines has at least one inline comment (over 30: about one
    per 10 lines). Comments explain why/intent, never restate syntax, and sit on their
    own line above the code -- no trailing comments. Explain non-obvious literals.
- Commits use `type(scope): subject` (`feat`, `fix`, `docs`, `test`, `perf`, `refactor`,
  `chore`) with a `Co-Authored-By:` trailer.
- Commit and push directly on `main`; don't create feature branches or PRs unless a specific
  workflow requires one.
