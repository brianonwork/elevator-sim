# Trial design

## How the experiment is built

**What the trials are for.** The prompt asks for a scheduler of our choice that serves everyone
eventually and keeps each person's total time low (explicit). As a bonus, it asks us to compare
schedulers, try express elevators and weigh fairness against efficiency (explicit). The trials
therefore aim to back one recommended scheduler with evidence, to find where that recommendation
changes with the traffic or the building, and to show what fairness costs.

We treated this as a controlled experiment with four groups of settings, all our choice
(implicit), one per section below:

- **Environment:** the building (floors, cars, seats per car) and the traffic (who travels
  where, how they arrive and how many riders arrive per tick).
- **Physics:** what a car can do and what a stop costs.
- **Protocol:** how a run's riders are drawn, when the run ends, and how the schedulers are
  judged.
- **Scheduler:** the thing under test, described in [the algorithms](3-algorithms.md).

A **cell** is one building with one traffic pattern. Within a cell, the first three groups are
identical for every scheduler, so any difference in the results can only come from the
scheduler. The best scheduler can depend on the building as well as the traffic. Round-robin,
for instance, ignores where the cars are, and that blind spot costs more when there are more
cars to choose badly among. So the result is **a winner per cell, not a single winner**, and
everything below is about choosing the cells.

## Environment

### Buildings

A building is the three things the prompt makes configurable: floors, cars and seats per car
(explicit). Which values to test is our choice (implicit). B1 carries the headline numbers, and
each of the others changes one thing about B1 to ask one question.

Each building also fixes how much traffic it gets. Every pattern runs at the same **demand**,
three quarters of the fleet's **nominal throughput**. Nominal throughput is the rate at which
every seat is used exactly once per round trip of the building: cars × seats ÷ (2 × floors)
riders per tick, where a **tick** is the time a car takes to travel one floor. It follows from
the building alone, with nothing measured. It is also the throughput of the hardest traffic
there is, everyone boarding at the lobby and riding most of the way up. The morning rush
therefore runs near the fleet's limit, while random floor-to-floor traffic, where a seat turns
over several times per sweep, runs well below it. Overload runs at one and a half times
nominal, double every other pattern.

Requests per run scale with the number of cars, at 100 per car.

| Name | Floors | Cars | Seats per car | Requests per run | Demand (riders per tick) | Overload demand (riders per tick) | Description |
|---|---|---|---|---|---|---|---|
| B1 | 60 | 4 | 8 | 400 | 0.20 | 0.40 | The reference building, which each of the others varies in one way: a 60-floor downtown office tower with a typical bank of four elevators, tall enough for the prompt's rider bound for floor 51 |
| B2 | 60 | 2 | 8 | 200 | 0.10 | 0.20 | A 60-floor office tower served by only two elevators, so a scheduler has few cars to choose between |
| B3 | 60 | 8 | 8 | 800 | 0.40 | 0.80 | A 60-floor office tower with a generous bank of eight elevators, so a wrong choice of car costs more |
| B4 | 30 | 4 | 8 | 400 | 0.40 | 0.80 | A modest 30-floor office block, to test whether a shorter building changes which scheduler wins |
| B5 | 100 | 4 | 8 | 400 | 0.12 | 0.24 | A 100-floor supertall skyscraper, to test whether a taller building changes which scheduler wins |
| B6 | 60 | 4 | 4 | 400 | 0.10 | 0.20 | A 60-floor tower with cramped four-seat cars, so a scheduler that ignores capacity is punished |
| B7 | 60 | 4 | 16 | 400 | 0.40 | 0.80 | A 60-floor tower with large, freight-size cars that rarely fill, so capacity almost never binds |

### Traffic patterns (8)

Each pattern copies a kind of real traffic and asks one question about the schedulers, such as
"do full cars leave people stranded for long?" Except during lunch's bursts, people arrive at
random at a steady average rate (a *Poisson* process), so several can arrive in the same tick.

| Pattern | Who travels where | Guess |
|---|---|---|
| Baseline | Random floor to random floor | Schedulers finish close; the yardstick for the rest |
| Morning rush | Lobby up to any floor, building up over the first quarter | Round-robin (cars in turn) falls behind, ignoring where cars are |
| Evening rush | Any floor down to the lobby | Spreading riders across cars beats filling one |
| Skewed two-way | 85% up from the lobby, 15% down to it | The fairness-tuned scheduler gives up a little average to cut the longest waits |
| Interior hub | To and from floor 30, from above and below | Nearest-car piles onto one side; ETA-cost balances |
| Lunch bursts | Bursts of 20, half up from the lobby, half down | Open: does the fleet catch up between bursts? |
| Overload | Skewed two-way's traffic at double demand, more than the building can handle; arrivals stop at 1,500 ticks and the run continues until everyone is delivered | How fast a scheduler clears the backlog separates them |
| Local + express | 60% hops of 1–3 floors, 40% lobby to floors 41–60 | The express scheduler pays off, but only when stops cost time |

## Physics

The engine fixes how a car moves: one floor per tick, no changing cars mid-trip, and every car
starting idle on floor 1.

**What a stop costs.** Nothing by default, because the prompt's clock counts only travel. The
experiments also try stops that cost time: 1 tick in every cell of big-bang-1, and 3 ticks on a
single cell for the stop-time pair (see [Experiments](#experiments)). Sweeps over demand and over
the fairness scheduler's own setting are [follow-ups](6-follow-ups.md).

**The starved-rider seat in the experiments.** Every scheduler shares the rule that a car keeps a
seat for a rider who has waited six round trips ([the algorithms](3-algorithms.md)). It is part of
the controller, so it is active in every run, and it matters for the schedulers that pile riders
onto one car. Rerunning every run with the rule switched off changes 254 of the 7,910: 171 on the
evening rush, 77 on local-plus-express and 6 on overload, almost all under nearest-car, express or
zone-based, and none under ETA-cost or its fair version. In those runs the rule shortens the
longest wait in 229 and lengthens the average trip in 175, since a seat held back is a seat not
used. No case changes its leader or its tied-for-best set, and 9 of the 791 scheduler-by-case
averages move by more than 1%; the largest is nearest-car on the evening rush in the four-seat
building with 1-tick stops, 16% slower with the rule. The rule makes every wait end; it does not
cap it near six round trips, because a car serves its starved riders one at a time. The longest
recorded wait is about six times the limit.

## Protocol

Every pattern's guess, in the last column of the traffic table, was written down before the
runs, so that luck could not pick whichever reading flatters a favourite. Wrong guesses stay
visible in [Findings](5-findings.md) rather than being quietly edited.

### Seeds

A run's riders are drawn at random from the pattern. A **seed** is the number that starts the
draw, and the same seed always rebuilds the same riders, so "seed 3 of the morning rush on B1"
names one fixed **list** of 400 people.

Every scheduler runs on the identical lists. One list is one roll of the dice, and a gap on a
single list could come from the roll rather than the scheduler. Because every scheduler shares
the roll, the schedulers can be compared list by list.

Every cell, on every building, gets ten lists. The bar for calling a gap real falls as the lists
grow: at three lists a lead must be about four times its list-to-list wobble to count, and at
ten a little over twice. Ten lists settle a gap with a margin without inflating the run count, and
using the same count everywhere gives a win the same meaning everywhere.

### The numbers each run records, and why

Every run records the numbers below. A rider's **trip** runs from asking for a car to arriving,
so it includes the **wait**, which runs from asking to being picked up. Every number counts
every rider in the run, including the first ones, served while the cars spread out from the lobby.

| Number | Unit | Statistic | What it measures |
|---|---|---|---|
| Average trip time (the number every pattern is judged on) | ticks | Arithmetic mean (central tendency) | The mean trip |
| Average wait | ticks | Arithmetic mean (central tendency) | The mean wait |
| 95-in-100 trip time | ticks | 95th percentile, nearest-rank (tail) | The shortest trip that at least 95 of every 100 riders took no longer than |
| 95-in-100 wait | ticks | 95th percentile, nearest-rank (tail) | The same mark for waits |
| Middle trip time | ticks | Median, nearest-rank (central tendency) | The trip half of riders beat; one very slow trip cannot pull it up, so a mean well above it signals a long tail |
| Middle wait | ticks | Median, nearest-rank (central tendency) | The wait half of riders beat |
| Quarter-mark trip times | ticks, a pair | First and third quartiles, nearest-rank (spread) | The trips a quarter and three quarters of riders took no longer than; the gap between them is the spread of the middle half |
| Quarter-mark waits | ticks, a pair | First and third quartiles, nearest-rank (spread) | The same two marks for waits |
| Spread of trip times | ticks | Population standard deviation (spread) | How far trips typically sit from the mean |
| Spread of waits | ticks | Population standard deviation (spread) | How far waits typically sit from the mean |
| Longest trip time | ticks | Maximum (extreme) | The slowest rider's trip |
| Longest wait | ticks | Maximum (extreme) | The slowest rider's wait |
| Worst trip per stretch | list of ticks, one per stretch | Time series of binned maxima | For each 150-tick stretch of the run in which anyone asked, the longest trip among those who asked in it; 2–20 stretches per run, to spot a fleet falling behind |
| Shortest trip time | ticks | Minimum (extreme) | The fastest rider's trip |
| Shortest wait | ticks | Minimum (extreme) | The fastest rider's wait |
| Computer time | seconds | Processor-time measurement, not a passenger statistic | Processor time our machine spent simulating the whole run, scheduler included; compare within one machine only |

**Why these numbers.**

- **Average trip time** is the prompt's stated goal.
- **The 95-in-100 trip** (also called the p95), the time 95 of every 100 riders beat, shows a
  typical bad day without letting one outlier swing it.
- **The longest trip** is the single worst rider, and the prompt says nobody may wait forever.
- **The middle, quarter-marks and spread** show what the average hides: whether most people did
  fine while a few waited a very long time, or everyone waited a little.
- **The worst trip per stretch** shows whether the fleet recovers, for example between bursts,
  or keeps falling behind.
- **Computer time** matters because a brilliant algorithm that is too slow is useless in a real
  elevator.

**Deciding whether a difference is real.** We compare two schedulers list by list with a
**paired t-test**. It asks whether the average gap is large compared with how much that gap
bounces around from list to list, at the two-sided 95% level for the number of lists. A gap that
clears that bar is **separated**; one that does not is **tied**. Tied means the test cannot tell
the two apart on ten lists, not that they have been shown to be equal. The ranges printed in the
tables are context only: two schedulers whose ranges overlap can still be a clear win.

Each comparison is tested on its own, with no correction for how many are made. Big-bang-0 alone
makes 56 cells times several scheduler pairs, so at the 95% level a handful of separations would
appear by chance even if no real gap existed. Large, repeated leads, such as ETA-cost against the
original baselines in all 56 cells, are not at risk from this. Isolated, small ones are, and
[Findings](5-findings.md) marks those as exploratory.

## Experiments

We ran three experiments with the settings above. A fourth, the fairness–efficiency trade-off,
reads their runs again.

### Big-bang-0

The foundational experiment runs every scheduler across every building and traffic pattern.
Eight patterns in seven buildings give 56 cells, and each cell is run by all seven schedulers on
the same ten lists, with stops priced at zero ticks: 56 × 7 × 10 = **3,920 runs**. It answers the
main question, which scheduler wins in each cell and where the winner changes with the traffic or
the building. Every headline number in [Findings](5-findings.md) comes from big-bang-0.

### Big-bang-1

Big-bang-1 is big-bang-0 with every stop costing 1 tick, the time a car would plausibly spend
opening its doors: the same 56 cells, schedulers and lists, another **3,920 runs**. It asks
whether the ranking survives once stops are no longer free, since a scheduler that makes many
stops pays for each one.

### The stop-time pair

With free stops, skipping floors saves nothing, so big-bang-0 alone cannot show the express
scheduler paying off. This experiment therefore reruns the local + express pattern on B1 with
3-tick stops: every scheduler on the same ten lists, **70 runs**. Big-bang-0's B1 cell for that
pattern serves as the free-stop half of the pair. Comparing the
express scheduler with ETA-cost at both stop costs shows whether reserving a car helps at all,
and whether it helps only once stops cost time.

### The fairness–efficiency trade-off

The prompt's bonus asks how fairness (nobody waits very long) trades against efficiency (a short
average trip). ETA-cost fair is the only scheduler with a setting for it: it is ETA-cost with
each rider's trip time squared before adding. The simplest test therefore compares the two on
runs already made, and needs **no new runs**.

- **Cells.** All 113 where both ran: big-bang-0's 56, big-bang-1's 56 and the 3-tick half of
  the stop-time pair.
- **Numbers.** Average trip for efficiency, and the 95-in-100 trip for fairness.
- **Test.** In each cell, the paired test on each number, ETA-cost fair against ETA-cost, over
  the same ten lists.
- **Reading it.** A trade is a cell where fair's 95-in-100 is separated and better while its
  average is separated and worse. With 226 tests at 95%, about 11 come out separated by chance
  alone, so a few scattered separations are noise.

**Totals.** 3,920 runs for each big-bang experiment plus 70 for the stop-time pair make
**7,910 runs**. The fairness–efficiency trade-off adds none.

Apart from the three building settings, every choice here, scoring rules included, is ours
(implicit).

---

[← The algorithms](3-algorithms.md) · [Overview](1-overview.md) · [Next: Findings →](5-findings.md)
