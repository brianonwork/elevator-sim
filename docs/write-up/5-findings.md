# Findings

## Reading the figures

Each experiment has its own folder of figures.

**Big-bang-0** (`figures/big-bang-0/`, free stops) and **big-bang-1** (`figures/big-bang-1/`,
1-tick stops) answer "which scheduler works for this pattern?" Each folder holds:

- `best-schedulers.png` ([big-bang-0](../results/figures/big-bang-0/best-schedulers.png),
  [big-bang-1](../results/figures/big-bang-1/best-schedulers.png)): patterns down, buildings
  across; each cell lists the schedulers tied for best on average trip, leader in bold, shaded
  where the set differs from B1's.
- `by-traffic/`: one page per pattern, measures down and buildings across. Each panel has a dot per
  list and a tick at the mean, per scheduler: blue is tied for best on that row, grey separated and
  worse, on one logarithmic scale per row. The dots show what an average hides.

**The stop-time pair** ([figure](../results/figures/stop-time-pair/local-plus-express-b1.png))
asks whether reserving a car pays once stops cost time: a pattern page with two columns, stops
costing 0 and 3 ticks on B1. If express paid off, it would turn from grey to blue across a row.

**The fairness–efficiency trade-off**
([figure](../results/figures/fairness-efficiency/trade-off.png)) asks whether ETA-cost fair gives up
average trip for shorter long waits: one bar per outcome over 113 cases. The bottom bar, the trade
itself, is 0. Per-case numbers are in `report.md`.

## How the comparison works

We compared seven ways of deciding which elevator answers each call. For each building and traffic
pattern, a **case**, we generated ten **lists** of requests and ran every scheduler on all ten, so
any difference in the results comes from the scheduler, not from luck in who showed up.

Time is counted in **ticks**; a car travels one floor per tick. A rider's **trip** runs from
pressing the button to stepping out, so it includes the wait. The **long waits** are measured by the
**95-in-100 trip**, the time 95 of every 100 riders beat. Unless we say otherwise, a number averages
the ten lists on the reference building, B1 (60 floors, four cars, eight seats), and counts every
rider.

To decide whether a gap between two schedulers is real, we compare them list by list with a paired
t-test ([trial design](4-trial-design.md#the-numbers-each-run-records-and-why)). A gap the test
accepts is **separated**; one it cannot tell from chance is **tied**, which does not prove the two
equal. Each test stands alone, uncorrected for how many we ran, so a lone small separation is a
hint, not a result. Full tables are in [report.md](../results/report.md), raw numbers in
[data/](../results/data/).

## The short version

- **Planning ahead wins.** ETA-cost, which predicts how each car's route would change before
  choosing one, beat the three original simple schedulers in every one of the 56 cases.
- **Most of its edge comes from one idea.** A "send the nearest car" rule comes within a median 4.7%
  of ETA-cost once "nearest" is measured in time rather than floors.
- **A dedicated express car rarely pays,** and does worse, not better, when stops cost time.
- **Costing each stop a tick changes little.** ETA-cost still beats the three original simple
  schedulers everywhere, and its lead over nearest-car by time stays near 5%.
- **A "fairer" version of ETA-cost is free but small.** It never trades a slower average for
  shorter long waits, and only once stops cost time does it cut the long waits at all often.

## Big-bang-0: every scheduler in every building

All seven schedulers ran eight traffic patterns in seven buildings, 56 cases, with free stops.

### Who wins, and by how much

ETA-cost beat the three original simple schedulers (round-robin, nearest-car and zone-based) in
all 56 cases, and the paired test confirms each lead. Measured against the best of the three in
each case, its median lead on average trip time is **24%**. It ranges from 49%, for local plus
express traffic on the eight-car building, down to 8%, for the morning rush on the same
building, where round-robin already spreads a lobby queue well. In 18 cases the lead is under
20%.

A fourth simple scheduler, nearest-car by time, was added later. It was the best simple scheduler
everywhere, and counting it cuts ETA-cost's median lead to **4.7%**, with only one case over 20%.
(Each lead is measured against the *best* rival in its case.)

**Average trip time on B1**, in ticks; the lowest in each row is in bold.

| Pattern | ETA-cost | Nearest-car by time | Round-robin | Zone-based | Nearest-car |
|---|---|---|---|---|---|
| Baseline | **35.2** | 37.2 | 67.2 | 61.8 | 54.9 |
| Morning rush | 65.6 | **65.5** | 77.8 | 199.1 | 324.1 |
| Evening rush | **53.1** | 61.5 | 77.9 | 64.4 | 272.7 |
| Skewed two-way | **54.5** | 54.7 | 79.2 | 111.0 | 88.0 |
| Interior hub | **26.1** | 27.5 | 49.4 | 50.3 | 41.3 |
| Lunch bursts | **44.6** | 48.0 | 72.5 | 87.3 | 63.8 |
| Overload | **136.0** | 138.4 | 166.8 | 230.4 | 212.1 |
| Local plus express | **39.4** | 40.7 | 73.8 | 71.9 | 64.7 |

ETA-cost beats round-robin, zone-based and nearest-car on every row, on all ten lists. Against
nearest-car by time, the morning rush, skewed two-way and overload rows are ties; the other five
are separated.

### Why measuring in time matters

Plain nearest-car counts distance in floors, so a car two floors past you and heading away, or
already full, still looks close. Nearest-car by time picks the car that would actually reach you
first, using ETA-cost's route prediction. That single change beats plain nearest-car on all eight B1
patterns, closes a median 96% of its gap to ETA-cost, and cuts the evening rush's 95-in-100 trip
from 1,218 ticks to 117.

The two leaders tie where nearly every trip starts or ends at the lobby (the morning rush, skewed
two-way and overload): there, "which car reaches this rider first" and "which car delays everyone
least" pick the same car. ETA-cost wins on mixed trips, because only it counts the delay a pickup
imposes on riders already aboard: by one to three ticks, or 8 ticks (16%) on the evening rush.

Nearest-car by time is ahead, separated, in just three cases, the morning rush on the 30-floor,
eight-car and sixteen-seat buildings, each by under a tick. They are the only cases where a simple
scheduler beats ETA-cost, and about what chance alone would produce ([caveats](#caveats)).

### How the simple schedulers behave

**Nearest-car** was the worst. It keeps piling riders onto whichever car is closest even when
that car is full or heading away. On the evening rush its 95-in-100 trip was 11 times
ETA-cost's.

**Round-robin**, which hands calls to the cars in turn, is strong only when calls come from one
place: in the morning rush it came within 19% of ETA-cost and beat nearest-car fourfold. On random
traffic (the baseline pattern) it was the worst of all, because when cars are often idle, where each
stands is the whole decision. There ETA-cost's average trip is nearly half round-robin's (35 against
68 ticks).

**Zone-based**, which gives each car a home band of floors, fails the morning rush, at three times
ETA-cost's average trip, because cars sit parked away from the lobby. It comes last on four other
patterns too. On the evening rush its average is the best of the three original simple schedulers,
but its long waits are second worst of the six that treat every car alike, ahead only of
nearest-car.

### The express car

The express scheduler gives one car (two in the eight-car building) every trip that begins and ends
in the lobby or the top third. That car ends up with far more than its share: on one
local-plus-express list it carried 230 of 400 riders, including 79 short hops, while the three local
cars carried 74, 50 and 46. Express is worse than ETA-cost on all eight of B1's patterns; on its own
pattern its average trip is 141 ticks against 39.

It wins 3 of the 56 cases, apparently where its share of the trips matches its share of the fleet (a
reading we did not test): overload on the two-car building, where it is half the fleet (average trip
100 ticks against 149), and skewed two-way and morning rush on the eight-car building, which has two
express cars. On the two-car building's morning rush it is 13% worse. See
[follow-ups](6-follow-ups.md#tuning-the-express-scheduler).

### Overload, taller buildings and bigger cars

**Overload.** For 1,500 ticks riders arrive at twice the usual rate, more than any fleet here can
keep up with; then arrivals stop and the run continues until everyone is delivered. The backlog
lengthens every trip (ETA-cost averages 136 ticks on B1, against 55 for the same traffic at normal
demand) but barely changes the order: ETA-cost and nearest-car by time tie, round-robin is 23%
slower, and nearest-car, express and zone-based roughly 45% to 70% slower.

**Height.** At 30, 60 and 100 floors the order barely moves and ETA-cost's lead stays steady (37%,
36% and 40% on the baseline pattern; 16% on the morning rush at all three), since demand grows with
height by rule.

**Bigger cars.** We guessed sixteen-seat cars would stop lunch bursts from separating the
schedulers. Half right: the longest-trip gap between ETA-cost and nearest-car narrows from 2.2 times
to 1.4, but all four simple schedulers stay separated. Bigger cars leave fewer riders behind; they
do not shorten the wait for a sweep to come back.

### What it costs to compute

ETA-cost replays every car's future route for each new rider, so it does more work: over
big-bang-0's runs, about 2.1 times as long as the three original simple schedulers (0.19 against
0.09 seconds). Pairing runs on the same list is fairer: the median pair costs 1.6 times as much and
the worst, overload on the 30-floor building, 10 times. Nearest-car by time costs 1.4 times and
express 1.5 times. The cost grows with the queue of waiting riders.

## Big-bang-1: the same with 1-tick stops

Big-bang-1 repeats big-bang-0 with every stop costing a tick. Trips get longer (on B1, ETA-cost's
baseline average rises from 35.2 to 40.9 ticks, its overload average from 136.0 to 197.7), but the
ranking holds. ETA-cost again beats round-robin, nearest-car and zone-based in all 56 cases,
separated every time, by a median **25%** (from 8% to 51%); counting nearest-car by time, by
**5.3%**. Nearest-car by time ties ETA-cost in 12 cases and leads none: its three sub-tick wins in
big-bang-0 do not reappear.

The leader changes in 26 cases: 16 swap between ETA-cost and ETA-cost fair, 7 between ETA-cost fair
and nearest-car by time, and 3 between ETA-cost fair and express. The tied-for-best set changes in
only 18. Express leads two cases, overload on the two-car and sixteen-seat buildings, and is worse
than ETA-cost in 54 of 56. ETA-cost costs more to compute: 3.3 times the simple schedulers on
average, 1.9 times for the median pair, up to 21 times (overload on the 30-floor building).

## The stop-time pair: 3-tick stops and the express car

With stops free, skipping floors saves nothing, so we reran local plus express on B1 with 3-tick
stops, expecting the express car to pay off. It did the opposite. ETA-cost's average trip rose by a
third (39 to 53 ticks) while express's more than doubled (141 to 330): the car that owns the band
makes the most stops and pays for each. Every comparison went against express on all ten lists.

| Stops cost | Measure | ETA-cost | Express |
|---|---|---|---|
| 0 ticks | Average trip | 39.4 | 140.8 |
| 0 ticks | 95-in-100 trip | 94.0 | 478.6 |
| 0 ticks | Longest trip | 116.8 | 599.8 |
| 3 ticks | Average trip | 52.8 | 329.5 |
| 3 ticks | 95-in-100 trip | 119.9 | 1,183.1 |
| 3 ticks | Longest trip | 159.1 | 1,319.8 |

With 3-tick stops ETA-cost alone is best; ETA-cost fair and nearest-car by time trail it by 4
ticks, separated.

## Fairness against efficiency

ETA-cost fair is ETA-cost with one change: before adding up everyone's trip times, it squares
each one, so a single long wait counts for more than several short ones. The aim is to spare the
unluckiest riders, perhaps at some cost to the average. It never made that trade, and with free
stops it almost never made any difference.

**Why it often cannot matter.** The test we chose in advance, skewed two-way traffic on B1 with free
stops, found identical results on nine of ten lists. That is arithmetic, not luck: a new pickup
there is almost always added to the end of a car's route, so each car's extra cost is a single
number, and squaring one number never changes which car is cheapest. The fair version chooses
differently only when a pickup is slotted into the middle of a route.

**What happened across all 113 cases** (both big-bang experiments plus the 3-tick run;
[figure](../results/figures/fairness-efficiency/trade-off.png),
[design](4-trial-design.md#the-fairnessefficiency-trade-off)), comparing the two on average trip
and on long waits:

- **No real difference: 87.** In 11 the two gave identical results on every list; in the other
  76 the gap was too small to tell from chance.
- **Fair faster on average: 3**, and **fair slower on average: 6**, with long waits unchanged.
- **Fair cut the long waits: 17**, with the average unchanged. Only 4 of these have free stops;
  13 are big-bang-1 cases.
- **Shorter long waits bought with a slower average: 0.**

With free stops (big-bang-0 alone), 7 real changes in 112 comparisons is close to the six chance
would give. Over all 113 cases there are 32 in 226, nearly three times the 11 expected, and the
excess is long waits cut at 1-tick stops: once a stop costs time, squaring does change which car is
cheapest. Pooling big-bang-0's 560 runs gives the free-stop picture: squaring cut the longest trip
by about 1.3 ticks (about 1%) and the 95-in-100 trip by 0.4, at no measurable cost to the average.
That small gain survives a stricter test that first reduces each case to one average, and comes from
mixed traffic (evening rush, local plus express and lunch bursts, about 3 ticks off the longest
trip); in the morning rush the fair version never chose a different car.

The trade does exist. A six-request example built to force it shows it plainly: the average
rises from 17.2 to 19.0 ticks while the longest trip falls from 32 to 30.

```bash
uv run elevator-sim data/fairness_requests.csv --elevators 2 --floors 20 \
  --capacity 8 --stop-time 1 --scheduler eta-cost-fair
```

The effect stays small because every scheduler shares one driving rule: a car finishes its sweep
before turning back, so a rider travelling against it waits for the return trip whichever car is
chosen. The next step is to let a car turn around for someone who has waited too long
([follow-ups](6-follow-ups.md)).

## Caveats

- Each separated-or-tied call is a 95% test made on its own, and there are hundreds, so about one
  comparison in twenty with no real gap still comes out separated. Nearest-car by time's three
  sub-tick morning-rush wins in big-bang-0 are about what chance would produce. ETA-cost's lead over
  the three original simple schedulers, in every big-bang-0 case and on every list, does not depend
  on this.
- Every pattern runs at one fixed demand, which puts the morning rush near the fleet's limit
  and random and hub traffic well below it. How each gap changes with demand is untested. The
  demand rule is the same for every scheduler, so it favours none.
- Every pattern is judged on all its riders, not a subgroup such as skewed two-way's downward riders
  or local plus express's lobby-to-top trips.
- Overload's arrivals stop at 1,500 ticks and the run continues until everyone is delivered, so
  every rider is counted.
- Stops cost nothing in big-bang-0, the headline experiment, because the prompt prices only
  travel; big-bang-1 and the stop-time pair test what changes when they do not.
- Zone-based's zones and parking floors, and express's band and share of the fleet, are
  untuned.
- Computer times come from one machine and compare only with each other.

---

[← Trial design](4-trial-design.md) · [Overview](1-overview.md) ·
[Next: Follow-ups →](6-follow-ups.md)
