# Follow-ups

These are our next steps: improvements we would make with more time, then two experiments we
designed but did not run. Other pages link here rather than keeping their own lists.

## Improvements with more time

### Hybrid schedulers

Every scheduler we built applies one rule to the whole fleet for the whole run. A hybrid mixes
rules, in one of two ways.

The first splits the fleet, with different cars following different rules at once. Express is a
crude example: one car takes every lobby-to-top trip while the rest run ETA-cost, and it wins only
where its share of trips matches its share of the fleet. Richer splits could keep one car shuttling
from the lobby during a rush, or pair zone-based's parking with ETA-cost's choice of car.

The second splits the day. A fixed timetable could switch rules for a known morning peak, lunch and
evening; an adaptive scheduler would read the traffic as it arrives, such as the share of recent
calls starting at the lobby, and switch to suit.

Big-bang-0 tempers expectations: ETA-cost leads or ties almost everywhere, and nearest-car by time
is ahead only on three buildings' morning rush, by under a tick. The promise is in pairing ETA-cost
with what it lacks. None of this is tested. A hybrid needs no engine change, since riders keep the
car they were given and a switch affects only new riders. Testing one does need traffic that changes
during a run, which none of our eight patterns has.

### Break direction commitment for a starved rider

The trials point straight at this. All seven schedulers share one driving rule: a car finishes its
sweep before turning back, so a rider travelling against it waits whichever car is chosen. The
fairness power trims only about 1.3 ticks off the longest trip pooled over big-bang-0's 560 paired
runs, about 1%, because it changes which car is chosen while the problem lies in how cars drive.
Letting a car reverse for a rider who has waited past a threshold could be tested on the existing
patterns. The [seat rule](3-algorithms.md#no-rider-left-behind-a-seat-for-a-starved-rider) already
ends every wait; this would shorten the long ones.

### More seeds on the cells the paired test does not separate

Our most consequential correction came from generalising a result measured in one cell. With ten
lists everywhere, no original baseline ties the leader in any of the 56 cells, but nearest-car by
time ties in 19 and leads by under a tick on three buildings' morning rush. Those ties and wins, and
express's overload win on the two-car building, were found after the fact and need more lists before
anyone relies on them; doubling the lists there is the cheapest real evidence left.

### Reassignment before boarding

Every request is bound to one car the moment it is made, as the prompt's destination-dispatch model
specifies: riders enter their destination and are told their car at once. Real systems re-plan while
a passenger waits, and overload's worst waits belong to riders bound early to a car that then
filled. Assignment could be rerun as conditions change, keeping the car shown to the passenger
stable.

### Dynamic sectoring instead of static zones

Zone-based loses the morning rush because its zones are fixed: cars sit parked away from the
lobby while the queue there grows. Switching zoning off at the peak is a time-based [hybrid
scheduler](#hybrid-schedulers).

### Sky-lobby transfers

The engine cannot move a passenger between cars, so a rider from floor 5 to floor 45 rides one local
car all the way. A transfer floor would allow true express and local fleets with a hand-off, as real
tall buildings use.

### Tuning the express scheduler

Express has two settings, both fixed by rule: one car in four is express, and it owns its band, the
lobby plus the top third, outright. Neither was tuned, and on its own pattern the band hands the
express car more than half the riders. A narrower band, band trips only above a minimum length, or a
larger share of the fleet would bring its load closer to its share of capacity, where its two-car
overload win suggests the gain lies; a demand sweep would show where it starts to pay.

### Per-group reporting

Every pattern is judged on whole-run numbers, which hide the riders travelling against the crowd.
Tagging each rider with their **group**, the kind of trip they take (such as the 15% going down in
skewed two-way), and reporting each group's average, 95-in-100 and longest trip would show who bears
the cost. An earlier version had it; restoring it is small.

### Cheaper marginal cost

For every new rider, ETA-cost replays each car's route with and without them to find their marginal
cost, what they would add to the car's total. That costs about 2× the simple schedulers on
big-bang-0's mean, about 1.6× on the median paired run and up to 10× on the worst one
([findings](5-findings.md) has the definitions), rising with the backlog. Past saturation it grows
roughly with the cube of the backlog: 400 requests at tick 0 on one single-seat car took 69 seconds
of CPU against 1 for round-robin. Caching routes within a tick, or limiting how far ahead the
replay looks, should remove most of that cost without changing any decision, though that needs
checking.

## Deferred experiments

The study as reported is big-bang-0, big-bang-1 and the stop-time pair ([trial
design](4-trial-design.md)). Two further sweeps have commands but have not been run at the current
arrival rate, so there are no numbers to quote.

### Demand sweep

**What it would test.** The big-bang experiments run every pattern at one demand: 0.75 of the
fleet's nominal throughput, the rate at which every seat is used once per round trip. Schedulers
often tie when a building is quiet and pull apart near its limit, so one level can hide a point
where their order flips. Random and hub traffic run far below that limit and the morning rush near
it, so only a sweep can show whether small gaps widen under load, and whether the morning-rush tie
between ETA-cost and nearest-car by time survives.

**Design.** `uv run elevator-trials sweep` runs B1 on three patterns (baseline, morning rush and
overload) at six demands from 0.4 to 1.2 of nominal throughput, with 10 lists and every
scheduler: 1,080 runs, written to `docs/results/data/load_sweep.csv`.

### Fairness exponent sweep, with stops that cost time

The [fairness–efficiency trade-off](4-trial-design.md#the-fairnessefficiency-trade-off) compares
only powers 1 and 2 of ETA-cost fair, which raises each rider's trip time to a power before adding.
This sweep would test powers between and beyond, in one cell chosen so that they can matter.

**Why the first attempt cannot answer it.** It ran on skewed two-way traffic at B1 with free
stops, at powers 1, 1.5, 2 and 3, on 5 lists, and all four gave byte-identical runs. When a pickup
is added to the end of a car's route, each car's extra cost is a single number, and raising it to
any power leaves the cheapest car the cheapest. The hand-built `data/fairness_requests.csv`, with
1-tick stops, does show the power trading average for longest trip.

**What it needs.** A stop-time setting on `run_fairness` (one small parameter), then
`uv run elevator-trials fairness`, which writes one row per list to
`docs/results/data/exponent_sweep.csv`.

---

[← Findings](5-findings.md) · [Overview](1-overview.md)
