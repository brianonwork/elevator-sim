# The algorithms, and why

A scheduler makes one decision: which car takes a new rider. The seven we compared share everything
else.

## What all seven share

Every scheduler assigns a new rider to one car the moment they ask, for good. Every car then follows
the same driving rules, known as LOOK:

- A car keeps its direction while any of its stops (a rider's destination or a pickup) lies
  ahead, and turns around only when all of them are behind it.
- It moves to the nearest stop ahead.
- At a floor it picks up only its own riders who are going its way, earliest-assigned first,
  until it is full. Anyone left over waits for the car's next visit.
- An idle car heads to its nearest stop, or stays put if it has none. Zone-based is the
  exception, described below.

The simulator enforces capacity for every scheduler, and none can see riders who have not asked yet.
They differ only in which car gets a new rider, and whether they consider how full it is.

### No rider left behind: a seat for a starved rider

The prompt says no passenger should wait indefinitely, and LOOK alone does not ensure it: a car can
fill up on floors before a rider's and pass them full on every trip while new riders keep arriving
there. So once a car's longest-waiting rider has waited six round trips (720 ticks on 60 floors; a
tick is the time to travel one floor), the car keeps a seat free for them, and every wait ends.

[Follow-ups](6-follow-ups.md) lists two ways to shorten those waits: [breaking direction
commitment](6-follow-ups.md#break-direction-commitment-for-a-starved-rider) and [reassignment before
boarding](6-follow-ups.md#reassignment-before-boarding).

## Round-robin

Cars take turns in number order: 0, 1, 2, 3, then 0 again, regardless of each car's position,
direction or load.

## Nearest-car

This scheduler sends the car whose current floor is closest to the rider's. A tie goes to a car
heading toward the rider, then to the less busy one. That tie-break is its only look at load,
so a close car that is heading away or already packed still wins.

## Nearest-car by time

This also sends the nearest car, but measures distance in time. It runs the LOOK rules forward on a
copy of each car and picks the one that would reach the rider first. The simulation boards riders
only while seats are free, so a car that is full or heading away counts as far.

## Zone-based

The floors are split into equal, back-to-back bands, one per car. With 60 floors and 4 cars, car
1 owns floors 1–15, car 2 owns 16–30, and so on.

- A rider goes to the car that owns their starting floor.
- Capacity matters only as a fallback: if that car's riders aboard plus those waiting for it reach
  1.5 times its capacity, the rider goes to the nearest car.
- A zone is a preference, not a fence: a car drives anywhere its riders need to go.
- An idle car returns to the middle floor of its zone, which keeps the fleet spread out.

## ETA-cost (our main algorithm)

ETA-cost sends the car the new rider would delay least, counting everyone. For each car it runs the
forward simulation twice, without the rider and with them, and adds up the predicted trip time of
every passenger the car serves. The rider goes where that total rises least, so the choice weighs
both how soon they are served and how much they slow everyone else. Because the simulation respects
free seats, a filling car costs more.

## ETA-cost fair

This works like ETA-cost but squares each person's trip time before adding. One rider at 100 ticks
then counts 10,000, while ten riders at 10 ticks count 1,000 together. The aim is to protect whoever
is having the worst trip, at a small cost to the average; [Findings](5-findings.md) shows how small
the effect turned out to be.

## Express

The prompt's bonus asks for express elevators that skip floors. This is ETA-cost with the fleet
split: one car in four, at least one, is an express car. Every trip with both ends in the lobby or
the top third (floors 41–60 of 60) goes to an express car; every other trip goes to a local car.
Within each side, ETA-cost's total, capacity included, picks the car. Results are in
[Findings](5-findings.md).

---

[← Code architecture](2-code-architecture.md) · [Overview](1-overview.md) ·
[Next: Trial design →](4-trial-design.md)
