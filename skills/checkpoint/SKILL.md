---
name: checkpoint
description: Save a track's working state and file what was learned into the shared notes, in one call. Use after a result, a dead end or a decision, every hour or two, before a compaction, when a GENIE hook asks for it, before a handover, or when the human says checkpoint.
---

# Checkpoint

What lives only in your conversation is lost at the next compaction. One call
saves the state and files what is durable.

## Think first, from what is already in context

1. What was learned, decided, scored or ruled out since the last checkpoint?
   A lesson needs its why. A decision needs the rejected option. A dead end
   counts, because it stops the next session from retrying it.
2. Is it already in a finding the digest lists? Unchanged: skip. Refined or
   contradicted: same slug, it appends. New: a new slug.
3. Nothing durable: `>>> none: <reason>`.

Distill. Never paste logs. Every number names its source.

## One call

```bash
genie checkpoint --track <slug> "<short topic>" <<'ENTRY'
## Goal
The current ask, in one or two lines.

## Done
- [x] what is finished, with paths, commits and scores

## Next
The single next step first. Then the rest.

## Blocked
What waits on the human or another session. Or "None".
===DURABLE===
>>> finding api-batch-of-64-is-fastest | 64 rows per call beats 16 and 256 | status: confirmed
## What we tried
## What happened
41.2 s against 86.5 s, commit a1b2c3d, `make bench`.
## So what
Keep 64.
>>> decision Batch size 64 | 2.1x faster, same output. Rejected: 256, it times out | ann
>>> board api | local: 41.2 s | next: cache the parse
>>> score 41.2 s | 44.0 s | a1b2c3d | batch 64
ENTRY
```

| Directive | Effect |
|---|---|
| `>>> finding <slug> \| <summary> \| status: draft/confirmed/dead` and a body | a note in `notes/findings/`, listed in the hub. An existing slug gets an update |
| `>>> append <path>.md#<Heading>` and lines | appends to an existing note |
| `>>> decision <what> \| <why> \| <who>` | a row in `notes/decisions.md` |
| `>>> board <track> \| local: x \| outside: y \| next: z \| owner: w` | edits that track's status row, any subset |
| `>>> score <local> \| <outside> \| <commit> \| <note>` | a row in `tracks/<slug>/SCORES.md` |
| `>>> none: <reason>` | nothing durable this time |

`draft` was seen once, `confirmed` was reproduced, `dead` failed and says why.

It checks every directive before writing anything, keeps the last 3 states in
`STATE.md`, lints the notes and prints the git commands for the files it
touched. It never commits. After a compaction the newest state is given back
to you. Use `--track none` for work outside any track. That saves no state.
