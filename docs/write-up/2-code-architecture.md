# Code architecture

A map of the code: the modules, the engine–scheduler contract, adding a scheduler, and the tick
order. How to run it is in the [README](../../README.md); what each scheduler does is in
[the algorithms](3-algorithms.md).

## Layout

Everything lives in `src/elevator_sim/`, in three parts: the engine, the built-in schedulers
(`algorithms/`) and the evaluation harness (`trials/`). Nothing in the engine or the schedulers
imports the harness.

**The engine**

- **`__init__.py`**: the public API. Re-exports what a user needs to build a building, plug in
  a scheduler and run it, so `from elevator_sim import ...` is enough.
- **`models.py`**: the data types. Requests and building settings, the engine's own live
  passengers and cars, and the frozen, read-only copies of them that schedulers are handed
  each tick.
- **`scheduler.py`**: the contract between engine and scheduler: the `Scheduler` interface
  and `CarAction`, one car's orders for a tick. No algorithm code.
- **`simulation.py`**: the tick loop. Releases requests when they are due, lets riders off,
  asks the scheduler for a plan, checks every part of it, boards riders and moves cars. It
  makes no choices of its own.
- **`csv_io.py`**: reads the request CSV and writes the two output logs, car positions per
  tick and one timing row per passenger.
- **`stats.py`**: turns finished passengers into the end-of-run summary: wait, travel and total
  time as min, quartiles, p95, max, mean and spread, plus each car's riders and share of ticks
  moving, and the peak queue.
- **`cli.py`**: the `elevator-sim` command. Parses arguments, wires the pieces together and
  runs; no logic of its own.

**The schedulers (`algorithms/`)**

- **`__init__.py`**: `BUILTINS`, the plain dict mapping each scheduler name to a factory.
  Adding a name here is all it takes to add a scheduler.
- **`controller.py`**: how a single car behaves once it has its riders: the LOOK sweep rules,
  who boards, where it heads next. Also replays those rules forward to predict arrival times.
- **`policies.py`**: the assignment rules, one per scheduler, each deciding which car a new
  request goes to. Pure functions of the snapshot, so they are easy to test and compare.
- **`dispatch.py`**: glues a policy to the controller. Binds each request to its car for
  good, remembers every car's pickups and heading, and turns that into the tick's plan.

**The evaluation harness (`trials/`)**

- **`traffic.py`**: generates request files: where trips go (flows) and when people arrive
  (arrival processes), reproducible from a seed so every scheduler sees identical traffic.
- **`presets.py`**: the experiment's settings: the eight traffic patterns, the seven
  buildings, the demand rule and the seeds.
- **`runner.py`**: runs the experiments (every pattern, building, variant, scheduler and seed)
  in parallel, one row per run in `runs.csv`, resuming where it left off; also the two follow-up
  sweeps.
- **`metrics.py`**: the worst trip per time window, which shows whether a fleet keeps up or
  falls behind.
- **`paired.py`**: the statistics. Compares two schedulers seed by seed and decides whether
  the gap is real, using a paired t-test.
- **`report.py`**: reads the results CSV back and writes `docs/results/report.md` and the
  figures.
- **`cli.py`**: the `elevator-trials` command, with one subcommand per step: `run`, `report`,
  `gen`, `sweep` and `fairness`.

## Engine versus scheduler

This line is the codebase's main design rule: the engine owns physics and constraints and makes
no scheduling decision, and everything a scheduler decides arrives through one call per tick.

A **Scheduler** is asked once per tick for a plan covering every car:

```python
class Scheduler(Protocol):
    def step(self, state: SimulationState) -> Mapping[int, CarAction]: ...


@dataclass(frozen=True)
class CarAction:
    target: int | None = None  # floor to move one step toward; None = stay
    board: tuple[str, ...] = ()  # ids of waiting requests at this car's floor to admit
```

`state` is an immutable snapshot: `time`, `config`, one `ElevatorView` per car (floor,
capacity, riders, `held_until`), every `waiting` request in request order, and the requests
`new` this tick. Cars missing from the plan stay put and board nobody. The engine raises
`SchedulingError` for a plan that names an unknown car, boards someone who is not waiting at
that car's floor, exceeds capacity, boards the same passenger twice, or targets a floor
outside the building. Assignment, direction, batching, reassignment, parking and express
service are all expressed through the plan, so none of them needs an engine change.

## The built-in schedulers

All seven are destination dispatch: a request is bound to one car the tick it appears and
never reassigned. They share one controller and differ only in the assignment policy.

- **Controller** (`controller.py`): LOOK with direction commitment. A car keeps its heading
  while any committed stop lies ahead. When stops remain only behind it, it still keeps the
  heading if a passenger at its floor wants to continue that way; otherwise it reverses. At a
  floor it boards only assigned passengers travelling its way, in assignment order, up to
  capacity. While its oldest pickup has waited `starve_limit` ticks (six round trips,
  `12 × floors`), it keeps one seat back for that pickup, which ends every wait.
  `simulate()` replays these rules on a copy of the car to predict each passenger's drop-off
  tick, its ETA.
- **Cost** (`policies.py`): `eta-cost` replays the controller for each candidate car with and
  without the new passenger and charges the difference in `Σ (dropoff − request_time) ** p`
  over everyone that car serves, `--stop-time` included. `p = 1` is exactly the prompt's
  objective; `eta-cost-fair` uses `p = 2`.
- **Express** (`policies.Express`): one car in four, at least one, owns every trip with both
  ends in the lobby or the top third (`top_band`, `express_car_count`); `eta-cost` picks the car
  on each side.

## Adding a scheduler

A scheduler is registered as a `BuildingConfig -> Scheduler` factory. The built-ins live in one
plain dict, `elevator_sim.algorithms.BUILTINS`, which `create` looks up and `available` lists.
Adding a key is all it takes, and `--scheduler` then offers the new name too. This example runs
as written:

```python
from elevator_sim import BuildingConfig, CarAction, Scheduler, Simulation, SimulationState
from elevator_sim import available, create, format_summary, summarize
from elevator_sim.algorithms import BUILTINS
from elevator_sim.csv_io import read_requests


class NearestCar:
    """Board whoever is waiting here, then head for the closest floor anyone needs."""

    def step(self, state: SimulationState) -> dict[int, CarAction]:
        plan: dict[int, CarAction] = {}
        claimed: set[str] = set()
        for car in state.elevators:
            board = tuple(
                r.id for r in state.waiting if r.source == car.floor and r.id not in claimed
            )[: car.free_capacity]
            claimed.update(board)
            wanted = [r.request.dest for r in car.riders] + [r.source for r in state.waiting]
            target = min(wanted, key=lambda f: abs(f - car.floor), default=None)
            plan[car.id] = CarAction(target=target, board=board)
        return plan


def build(config: BuildingConfig) -> Scheduler:
    return NearestCar()


BUILTINS["my-nearest-car"] = build

print("available():", available())
config = BuildingConfig(num_elevators=2, num_floors=60, capacity=8)
result = Simulation(
    config, create("my-nearest-car", config), read_requests("data/sample_requests.csv")
).run()
print(f"{result.ticks} ticks")
print(format_summary(summarize(result.passengers)))
```

```
available(): ['eta-cost', 'eta-cost-fair', 'express', 'my-nearest-car', 'nearest-car',
              'nearest-car-eta', 'round-robin', 'zone-based']
103 ticks
Passengers served: 3
                 min      q1  median      q3     p95     max    mean      sd
Wait time          0       0       0      10      10      10    3.33    4.71
Travel time       38      38      52      82      82      82   57.33   18.35
Total time        38      38      52      92      92      92   60.67   22.88
```

## Tick order at time `t`

1. Log every car's floor for time `t`.
2. Release requests with `time == t` (never earlier) into the waiting pool.
3. At each car, riders whose destination is the current floor get off. If anyone did, the
   car is held until `t + stop_time`.
4. Build the snapshot and ask the scheduler for the plan.
5. Validate the whole plan, so a bad plan changes nothing, then board exactly the passengers
   it lists, in the listed order. If anyone boarded, the car is held until `t + stop_time`.
6. Move each car one floor toward its target, unless it is held.
7. Advance to `t + 1`. Stop when all requests are released and everyone is dropped off.

---

[← Overview](1-overview.md) · [Next: The algorithms →](3-algorithms.md)
