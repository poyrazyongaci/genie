---
name: lab
description: Run a problem that has a score as a genie lab, with a trusted scorer, an append-only ledger and keep or revert on the number. Use when the human makes a wish, says genie or run the lab, or a session start line says "GENIE lab <track>". The script picks the next job from files, so any session can continue.
argument-hint: "<slug>"
allowed-tools: Bash(genie next *) Bash(genie ledger *) Bash(genie time *) Bash(genie gate list *) Bash(genie locks *)
---

# Lab

The lab folder `tracks/<slug>/lab/` is the memory. Every command takes
`--track <slug>`.

## The one rule

```bash
genie next --track <slug>
```

It prints the stage, the failing check and one job. Do that job. Ask it again
after every ledger row, after a compaction and at session start. Do not pick
the job yourself.

## Start

| Situation | Command |
|---|---|
| New problem | `genie wish "<problem>" --track <slug> --better lower` (or `higher`) |
| A track that already has scores and notes | `genie adopt --track <slug> --better lower` |
| A lab exists | `genie next --track <slug>` |

`adopt` writes only what the track's files prove. Fill the rest from `STATE.md`,
`SCORES.md` and the findings. Do not redo work they record.

## Stages

| # | Stage | Your job | Passes when |
|---|---|---|---|
| 0 | Preflight | `genie preflight` | tools and secret files are present |
| 1 | Plan | fill every field of `WISH.md` | no `TODO` left |
| 2 | Scorer | earn trust in the local score, see `references/scorer.md` | golden, order, noise and freeze pass |
| 3 | Baseline | score the base at T2, get one outside score | both rows are in the ledger |
| 4 | Scout | run `genie:scout`, `genie idea add` each result | 3 ideas with a first test |
| 5 | Iterate | the loop below | never, until the goal in `WISH.md` is met |

Tiers: T0 takes seconds, T1 is a subset, T2 is the full local score, T3 is the
outside score. All measure the same thing. A faster tier is a smaller or cached
run, never another metric.

## One iteration

1. `genie idea start <id>`
2. `genie brief <id>`, then do the work in a fresh worktree or hand it to a worker
3. `genie score --tier T0 --entry <path> --idea <id>`, then T1, then T2. Stop at
   the first `loss`
4. A T2 `win`: commit, then `genie keep <row> --evidence <commit>`. A loss:
   `genie reject <row>` and drop the worktree
5. `genie next`

A gain under twice the noise is a loss. Do not argue with it or tune around it.
A wrong row is superseded (`genie supersede <row>`), never edited.

## Other jobs `next` may give

| Job | Do |
|---|---|
| tune the loop | one phase eats half the time. Cache, stop early, shrink T1. Then `genie golden` proves the score did not change |
| scout again | 3 rejects in a row, or an hour with no gain. Scout, ask for a second opinion, add ideas, `genie scouted` |
| fix the scorer or rebuild T1 | tiers rank attempts differently. This outranks every idea |

## Parallel work

| Kind | Rule |
|---|---|
| read only | any number |
| one idea per worktree | only when `next` says fan out. It checks that the ideas name disjoint files |
| timing runs, submissions, the keep | `genie lock <name>` first, `genie unlock <name>` after |

A timing score taken while another run is going is not a score. Winners merge
one at a time, and each is scored again on the new base.

The ledger holds numbers. What the numbers taught goes into the notes with
`genie:checkpoint`.
