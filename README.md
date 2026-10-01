# Elevator System Simulation

This project simulates a bank of elevators, one tick at a time, where a tick is the time a car
takes to travel one floor. Each passenger states both their starting floor and their destination
when they call a car. The engine handles the physics: moving cars, holding them at stops and
enforcing capacity. A separate, swappable **scheduler** decides every tick where each car goes
and who boards it, so different strategies can be compared on identical traffic.

All seven built-in schedulers use **destination dispatch**: each rider is assigned one car the
moment they ask. The engine does not require this. A scheduler in another style, such as
**collective control**, where cars simply answer up and down calls as they pass, would plug in
without any change to the engine.

## Documentation

The design and results are written up in six parts, in reading order:

1. [Overview](docs/write-up/1-overview.md)
2. [Code architecture](docs/write-up/2-code-architecture.md)
3. [The algorithms](docs/write-up/3-algorithms.md)
4. [Trial design](docs/write-up/4-trial-design.md)
5. [Findings](docs/write-up/5-findings.md)
6. [Follow-ups](docs/write-up/6-follow-ups.md)

## How to run

The project needs [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned in `.python-version`,
and uv fetches it automatically.

```bash
uv sync
uv run elevator-sim data/sample_requests.csv --elevators 2 --floors 60 --capacity 8
uv run pytest
```

Arguments:

| Argument | Default | Meaning |
|---|---|---|
| `requests` | required | CSV of requests (see below) |
| `--elevators N` | 1 | number of cars |
| `--floors N` | 10 | floors are numbered `1..N`; every car starts on floor 1 |
| `--capacity N` | 8 | maximum riders per car |
| `--stop-time N` | 0 | ticks a car is held in place after anyone boards or alights (0 = stops are free) |
| `--scheduler NAME` | `eta-cost` | `eta-cost`, `eta-cost-fair`, `express`, `nearest-car`, `nearest-car-eta`, `round-robin`, `zone-based`, or any name you add to `BUILTINS` |
| `--positions-out PATH` | `positions.csv` | per-tick elevator positions log |
| `--passengers-out PATH` | `passengers.csv` | per-passenger timing log |
| `--max-ticks N` | sized from the input | abort (exit 1) if the run has not finished by then; the default allows for the last request's time plus a generous drain per rider |

Seven schedulers are built in, and [the algorithms](docs/write-up/3-algorithms.md) describes each
one. `eta-cost` is the default. `express` needs at least two cars and three floors.

## Input format

The input is a CSV file with one request per row (see `data/sample_requests.csv`). Columns may
appear in any order.

```
time,id,source,dest
0,passenger1,1,51
0,passenger2,1,37
10,passenger3,20,1
```

## Output

- **Positions log** (`--positions-out`): one row per tick, starting at time 0, giving every car's
  floor: `time,elevator_0,elevator_1,...`
- **Passenger log** (`--passengers-out`): one row per passenger:
  `id,request_time,source,dest,elevator,pickup_time,dropoff_time,wait_time,travel_time,total_time`
- **Summary** printed to the terminal, in three parts:
  - The number of passengers served.
  - A table of wait, travel and total time, where `wait = pickup - request`,
    `travel = dropoff - pickup` and `total = wait + travel`. Its columns are the minimum, first
    quartile, median, third quartile and maximum, then the p95 (the time 95 of every 100
    passengers beat), the mean and the standard deviation. Percentiles are nearest-rank, so each
    one is a time some passenger actually had. A mean well above the median means a few
    passengers had much longer trips than the rest.
  - The fleet's behaviour: how many riders each car picked up and the share of ticks it spent
    moving, so a lopsided split shows one car doing most of the work, followed by the peak number
    of passengers waiting at once and the tick at which it happened.

## Running the trials

The evaluation harness runs every scheduler on the same generated traffic, across eight traffic
patterns and seven buildings, once with free stops (big-bang-0) and once with 1-tick stops
(big-bang-1). [Trial design](docs/write-up/4-trial-design.md) explains what it
runs and why, and [findings](docs/write-up/5-findings.md) reports what it found. Its output goes
to `docs/results/`: `report.md` (generated tables), `data/` (raw CSVs) and `figures/`.

```bash
uv run elevator-trials run         # big-bang-0/1 + stop-time pair -> data/runs.csv (resumable)
uv run elevator-trials report      # docs/results/report.md + figures/
uv run elevator-trials gen <preset> --out requests.csv   # one request file to inspect
uv run elevator-trials sweep       # follow-up, not in the reported results: demand sweep
uv run elevator-trials fairness    # follow-up, not in the reported results: exponent sweep
```

The repository includes one request file per pattern for the reference building (60 floors, four
cars, eight seats per car) in `data/`: the first of the ten the experiments ran, written by
`gen <preset> --seed N` with N = 10000 for the first pattern, 20000 for the second, up to 80000.
Every other file is regenerated the same way from its seed.

The trial commands read and write `docs/results` under the working directory, so run them from the
repository root, or point them elsewhere with `elevator-trials --results-dir DIR <command>`.

## Time spent

4.5 hours. A surprising share of it went into refining the trial design and methodology.

## Assumptions, simplifications, and trade-offs

These are the key ones. The [overview](docs/write-up/1-overview.md#assumptions-and-constraints)
gives the full list, marking each as stated by the prompt or chosen by us, and
[trial design](docs/write-up/4-trial-design.md) covers every choice behind the experiments.

- A stop costs `--stop-time` ticks, 0 by default, because the prompt prices only travel.
- Requests are released strictly in time order; no scheduler sees the future.
- A request is bound to one car the tick it appears and is never reassigned, so a rider who does
  not fit waits for that car's next visit. After six round trips of waiting (`12 × floors`
  ticks), the car keeps a seat free for them, so every wait ends. It is not a tight cap: a car
  serves its starved riders one at a time, and [trial design](docs/write-up/4-trial-design.md#physics)
  reports how often the rule changes a run.
- Direction logic: a car carrying riders never reverses and picks up only people going its way,
  so a rider going against the sweep waits for the return pass.
- All cars start idle on floor 1. Any car can stop at any floor; express service is a
  scheduler's choice, not a setting of the fleet.
- `zone-based` uses equal, fixed zones and parks idle cars in them.
- Trials: every pattern runs at one arrival rate fixed by rule, `0.75 × cars × capacity /
  (2 × floors)` riders per tick (1.5× for overload), for every scheduler.
- Trials: overload's arrivals stop at tick 1,500 and the run continues until everyone is
  delivered, so every rider counts in full.

## What I'd improve with more time

[Follow-ups](docs/write-up/6-follow-ups.md) has the full list, with reasoning. The top of it:

- [Hybrid schedulers](docs/write-up/6-follow-ups.md#hybrid-schedulers) that split the fleet
  across rules, or switch rules through the day by timetable or by reading the traffic.
- [Break direction commitment for a starved rider](docs/write-up/6-follow-ups.md#break-direction-commitment-for-a-starved-rider),
  the fairness lever the trials point to.
- [More seeds on the cells the paired test does not separate](docs/write-up/6-follow-ups.md#more-seeds-on-the-cells-the-paired-test-does-not-separate).
- [Reassignment before boarding](docs/write-up/6-follow-ups.md#reassignment-before-boarding).
- Two deferred experiments: a [demand sweep](docs/write-up/6-follow-ups.md#demand-sweep) and a
  [fairness sweep with stops that cost time](docs/write-up/6-follow-ups.md#fairness-exponent-sweep-with-stops-that-cost-time).

## Requirements coverage

Each requirement in the take-home prompt, and where the code meets it.

| Requirement (prompt section) | Where | How |
|---|---|---|
| Discrete-time simulation of many cars (Background) | [`simulation.py`](src/elevator_sim/simulation.py) | `Simulation.step()` advances the building one tick |
| Serve every request eventually (Objective 1) | [`controller.py`](src/elevator_sim/algorithms/controller.py) | After `12 × floors` ticks of waiting, a car keeps a seat free for the rider |
| Minimize wait + travel time (Objective 2) | [`policies.py`](src/elevator_sim/algorithms/policies.py) | `eta-cost` picks the car that adds least to everyone's expected arrival |
| Honor capacity (Objective 3) | [`simulation.py`](src/elevator_sim/simulation.py) | The engine rejects any plan that boards more riders than free seats |
| Honor direction logic (Objective 3) | [`controller.py`](src/elevator_sim/algorithms/controller.py) | A loaded car never reverses and boards only riders going its way |
| Origin and destination given up front (System Type) | [`models.py`](src/elevator_sim/models.py) | `Request(time, id, source, dest)` |
| Rider assigned to one car at once (System Type) | [`dispatch.py`](src/elevator_sim/algorithms/dispatch.py) | Bound to a car the tick the request appears |
| Destination fixed after assignment (System Type) | [`models.py`](src/elevator_sim/models.py) | Requests are immutable; riders are never reassigned |
| One tick = one floor of travel (Time Modeling) | [`simulation.py`](src/elevator_sim/simulation.py) | A car moves at most one floor per tick |
| No real-clock syncing (Time Modeling) | [`simulation.py`](src/elevator_sim/simulation.py) | Time is a whole-number tick counter |
| Tick one unit at a time, through gaps (Time Modeling) | [`simulation.py`](src/elevator_sim/simulation.py) | Every tick runs and is logged, even with no requests |
| Python model (Requirements) | [`src/elevator_sim/`](src/elevator_sim/) | Python 3.12, standard library only |
| Configurable number of cars (Requirements) | [`cli.py`](src/elevator_sim/cli.py) | `--elevators`, no upper limit |
| Configurable number of floors (Requirements) | [`cli.py`](src/elevator_sim/cli.py) | `--floors`, numbered `1..N` |
| Configurable seats per car (Requirements) | [`cli.py`](src/elevator_sim/cli.py) | `--capacity` |
| A scheduler that meets the objectives (Requirements) | [`scheduler.py`](src/elevator_sim/scheduler.py) | Pluggable interface; seven built in, `eta-cost` by default |
| Function that takes a list of requests (Input) | [`csv_io.py`](src/elevator_sim/csv_io.py) | `read_requests()` parses the CSV; `Simulation` takes the list |
| Integer time, unique id, floors (Input) | [`models.py`](src/elevator_sim/models.py) | `validate_requests` checks ids are unique and floors in range |
| No peeking ahead (Input) | [`simulation.py`](src/elevator_sim/simulation.py) | A request is released only at its own tick |
| Log every car's floor from tick 0 (Output 1) | [`csv_io.py`](src/elevator_sim/csv_io.py) | Writes `positions.csv` (`--positions-out`) |
| One row per tick (Output 1) | [`csv_io.py`](src/elevator_sim/csv_io.py) | Columns `time,elevator_0,elevator_1,...` |
| Min, max and mean wait time (Output 2) | [`stats.py`](src/elevator_sim/stats.py) | Printed by `format_summary` with quartiles, p95 and SD |
| Min, max and mean total time (Output 2) | [`stats.py`](src/elevator_sim/stats.py) | The same table's `total` row, beside travel time |
| Other observations (Output 2) | [`stats.py`](src/elevator_sim/stats.py) | Pickups and time moving per car, peak waiting; `passengers.csv` |
| README: how to run (Deliverables) | [How to run](#how-to-run) | Commands, argument table and trial commands |
| README: time spent (Deliverables) | [Time spent](#time-spent) | 4.5 hours |
| README: assumptions and trade-offs (Deliverables) | [Assumptions](#assumptions-simplifications-and-trade-offs) | Key choices here; the full list in the overview |
| README: improvements (Deliverables) | [Improvements](#what-id-improve-with-more-time) | Top items here; the full list in the follow-ups |
| Visuals or statistics (Presentation) | [`docs/results/`](docs/results/) | `report.md` and `figures/` |
