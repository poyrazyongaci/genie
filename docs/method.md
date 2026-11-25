# The method

Ten rules. Each one answers something that went wrong in a real run.

## 1. The human talks to one session

With eight sessions and no hub, the human becomes the dispatcher, the courier
and the status page. The overseer takes those jobs. It relays direction word
for word and brings back one table.

## 2. The hub does no track work

A hub that edits code stops noticing that the approach is wrong. Its work is
judging, routing and writing down rulings.

## 3. One owner per piece of work

A track is a folder, an owner and a rule that decides success. One writer per
folder. Parallel ideas live in separate git worktrees. Helpers that only read
can be many.

## 4. One session touches the outside

Credentials, rate limits and the record of what was sent stay in one place. The
operator checks a request, acts once and reports. It never builds and never
decides. A request carries the question it answers, a prediction, and what each
outcome decides.

## 5. Memory is files

A conversation is compacted, restarted or lost. Three kinds of file outlive it:

| File | Holds |
|---|---|
| `tracks/<slug>/STATE.md` | where the work stands, for the next session |
| `notes/findings/`, `notes/decisions.md` | what was learned, decided and rejected |
| `lab/ledger.tsv` | every scored attempt, append only |

A dead end is worth a note, because it stops the next session from trying it
again. A decision without the rejected option gets made twice.

## 6. State is derived, never stored

`genie next` works out the stage from the files each time. A stored stage
drifts. Derived state cannot.

## 7. Earn the number before chasing it

A loop cannot judge itself. Before iterating, the check must reproduce known
results, rank known entries correctly, have its spread measured, and be frozen.
When the local number and the outside number disagree, that gap is the only
job. This was the most expensive lesson of the run behind this kit: three of
four tracks spent a night improving local numbers the outside score ignored.

## 8. Judge the approach, not only the result

Someone outside the track has to ask four things. What number decides? What is
its noise? If this works perfectly, is it enough? What would prove it wrong? The
overseer asks, and a second opinion from another model family asks the overseer.

## 9. Gates are a queue

Most "may I continue" questions are not gates. A push, a submission, a secret
and spending are. They wait in a list while other work goes on. Only the human
answers, and an approval covers one action unless it says standing.

## 10. Watch without a model

Polling at full context is the most expensive way to learn that nothing
happened. A script watches and wakes an agent only when something needs a look.

## What a day looks like

| Phase | Overseer | Tracks | You |
|---|---|---|---|
| Set up | opens listeners, names the tracks | write the goal and the check | describe the work |
| First hour | reads every plan | ship the simplest thing end to end | answer gates |
| Loop | rules on results, forwards requests | question, change, judge, checkpoint | read one table |
| Stall | halts, audits what is measured, asks for a second opinion | scout, or move to another track | decide forks |
| Away | runs the away list, holds what you kept | keep working | nothing |
| End | one table, open items, rulings | final checkpoint | pick |
