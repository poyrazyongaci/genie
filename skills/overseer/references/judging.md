# Judging an approach

Tracks drift into polishing a number that nobody else measures. Your job is to
notice from outside.

## Questions to ask of any plan

1. What number decides, and who measures it? If the local number and the
   outside number disagree, nothing else matters until that is explained.
2. What is the spread of that number when nothing changes? A gain under twice
   the spread is noise.
3. If this works perfectly, is it enough? Work out the ceiling first. A line
   that cannot reach the goal is closed before it is built.
4. What would prove it wrong, and what is the cheapest way to look?
5. Does the checker share code or assumptions with the thing it checks? A
   checker built on the same library agrees with itself.

## Rules to hand to a track that spins

1. One question at a time, written with its baseline and predicted effect
   before anything is built.
2. Only changes that could move the number by clearly more than its spread.
3. Judge locally on paired cases. The outside score confirms, it does not explore.
4. Stop a line after two inconclusive comparisons.
5. A time box per line, then a decision: keep, drop or change.

## Signs a fleet is spiraling

| Sign | Do |
|---|---|
| many submissions, flat score | halt submissions until a local check predicts one |
| local gains the outside score ignores | the only job is the gap between the two |
| every result is "promising" | ask for the prediction that was written before it |
| fixes to fixes | name the premise they share and attack it |
| long reports, no number | ask for one table: predicted against measured |

## When to spend

An outside attempt that is limited or cannot be undone needs a written
question, a prediction, and a reading for each outcome. A cheap and unlimited
one needs only the question.

## Stopping a line

Move the session, do not let it idle. A read-only audit of another track is a
good next job, because it has fresh eyes and cannot break anything.
