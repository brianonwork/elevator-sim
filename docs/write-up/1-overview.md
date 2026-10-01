# Teaching a simulated building to run its elevators

## The setup

We built a computer simulation of an office building's elevators. Time moves in "ticks," and one
tick is how long a car takes to travel one floor. A passenger types in where they are going, like
the touchscreen kiosks in newer buildings, and is assigned to one car right away; they cannot
switch later. The simulator never looks ahead at future passengers.

The program has two parts. The **engine** handles physics and rules: moving cars, enforcing
capacity and letting riders off. The **scheduler** is the brain that picks a car for each
passenger. Because the two are separate, we can swap brains and test them on the same building
with the same passengers. [Code architecture](2-code-architecture.md) shows how.

## Bottom line

Planning ahead with a cost model beats simple rules. Most of its edge over a naive "nearest car"
rule, though, disappears once that rule measures distance in time rather than floors. For
fairness, the next step is letting a car turn around for someone who has waited too long
([follow-ups](6-follow-ups.md)).

## Assumptions and constraints

**Explicit** means the take-home prompt says it. **Implicit** means the prompt was silent or
vague, so we chose.

Explicit, from the prompt:

- One tick is one floor of travel, and the clock moves one tick at a time.
- The number of cars, floors and seats per car can be changed.
- A rider enters origin and destination together, gets one car right away, and cannot change
  destination.
- No peeking at future requests.
- Serve everyone eventually, and keep wait plus ride time as low as possible.
- Respect car capacity and "direction logic."
- Round-robin, nearest-car, zone-based, express cars and fairness are optional bonuses.

Implicit, our choices:

- *Direction logic:* a car with riders never reverses, and it picks up only people going its way.
- A rider is never moved to another car. If their car is full, they wait for its next visit;
  once they have waited six round trips, the car keeps a seat free for them.
- Stopping takes no time by default (a stop time can be set), and every car starts idle on
  floor 1.
- Riders always get off at their floor, and a rider already at a car's floor can board at once.
- Riders arriving in the same tick are assigned in file order.
- No acceleration, and no changing cars mid-trip.
- Any car can stop at any floor. An express car is a scheduler's choice: the express scheduler
  keeps one car for trips within the lobby and the top third, and no other scheduler holds a car
  back.
- Zone-based uses equal, fixed zones.

Every choice about how we ran the experiments is also ours; see [trial design](4-trial-design.md).

## Read in order

1. [Code architecture](2-code-architecture.md): what each module does, the scheduler interface,
   adding a scheduler and the tick order.
2. [The algorithms, and why](3-algorithms.md): the seven schedulers we compared.
3. [Trial design](4-trial-design.md): buildings, traffic patterns, demand, request lists, run
   counts, the measurements, and how we decided a difference was real.
4. [Findings](5-findings.md): what the trials showed, and a guide to the figures.
5. [Follow-ups](6-follow-ups.md): later improvements and deferred experiments.

---

Overview · [Next: Code architecture →](2-code-architecture.md)
