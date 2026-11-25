# Field report

Where the method comes from. One 48 hour contest in November 2026, four
tracks, two people, hidden tests and a live leaderboard. Counts come from
session transcripts and the kit's own logs, from one person's machine. One
event is an anecdote, not a study.

## Before: many sessions, no hub

About 8 sessions over 4 tracks. In the first 6 hours the human typed about 70
process actions.

| Manual action | Count | Root cause |
|---|---|---|
| compact | 14 | auto compaction sat near 967K tokens of a 1M window |
| checkpoint | 11 | agents never checkpoint unprompted |
| approvals | 16 | 6 of them were "may I continue" |
| status questions | 8 | no board to look at |
| relays between agents | 8 | no mailbox |
| "continue" after a compact, retyped loop prompts | 8 | a manual compact ends the turn |
| pasted secrets, helpers briefed by voice | 4 | the human was the courier |

## After: overseer, owners, operator, hooks

20 hours, terminal sessions only.

| Session | Turns typed by the human |
|---|---|
| overseer | 57 |
| four track sessions together | 13 |
| two helper sessions | 32 |
| the session that maintained the kit | 19 |

The overseer sent 526 messages and ran 607 board and bridge commands. The two
periods are not a controlled comparison. The work had moved on.

## What worked

| Part | Evidence |
|---|---|
| checkpoint before compaction | 64 of 70 compaction requests that were let through had a fresh checkpoint. 220 were held first |
| carry on after compaction | 10 to 23 compactions in each of the six long sessions |
| one operator | outside attempts were queued and answered with the question they were sent for |
| judging the approach | one track had spent 13 attempts and was flat at 400 since the 7th. The overseer stopped submissions until a checker could predict one. The next scored attempt, 20 hours later, was 619 |
| second opinions | about 30 calls. Their effect on scores was not measured |
| watcher without a model | replaced a poll that cost about 16K token equivalents per call |

## What did not work

| Finding | What changed in the kit |
|---|---|
| the keep or revert loop never ran end to end: 138 ledger rows in three labs, 117 of them outside scores, 0 keeps | stated in the README. `genie next` still needs a better job for tracks that only have an outside score |
| three of four tracks polished local numbers the outside score ignored | scorer trust became stage 2, the gap between scores outranks every idea |
| the overseer had no written brief. Its rules lived in chat | `skills/overseer` |
| an agent could have answered its own gate | only the human can, enforced by a hook and by skills agents cannot invoke |
| two sessions that started the board together could crash one of them | found while testing this plugin, fixed |
| the session-start digest grew to 30 KB | capped, newest first |
| sessions that waited on a score went idle | "waiting is not a state" in the track skill |
| five heavy jobs on one track starved the others | locks, and one heavy job at a time |
| a usage limit looked like a stall | its own alert |

## Measured on the way

- Compaction starts near 87 percent of the configured window
- Median context was 215K tokens before a compaction and 99K at the first call
  after it. The base that is loaded again is the cost lever, not the threshold
- At the API prices of 2026-11-24 the six main sessions cost 41 percent cache
  writes, 32 percent output and 27 percent cache reads, summary calls not counted
- A background precompute can fire the hook near 195K for a compaction applied
  near 218K, so a checkpoint judged fresh may still miss some work

These are observations of one Claude Code version. They can change.

## Results

One track went from 400 to 619 points. One stood 18th of 381 on the
last reading. One cut its practice time by about a fifth and then entered a
slower, safer build. One did not improve. No first place is on record.
