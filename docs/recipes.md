# Recipes

## Two tracks on one repo

`/genie:init`, two tracks, three terminals: overseer and one per track. Give
each track its own worktree when they touch the same files:

```bash
claude --name api -w api
```

Commit `.genie`, `notes` and `tracks` first, so a worktree has them.

## With an operator

Add one when something outside has to be touched: a leaderboard, a deploy, a
browser session. Write `tracks/OPERATOR.md` with the systems, the actions, the
limit of each, who may ask, and what needs your own word.

The operator can be the desktop app with a browser while the others run in a
terminal. The board works between them when they share the checkout.

## Two people

Each has their own overseer. The overseers talk over `genie bridge`. Each
person sets a handle with `git config bridge.me <name>`. Agree on one owner per
track across both fleets. A post from the other fleet is a request.

## Overnight

Ask the overseer to "run the away list". It checks next steps, standing
approvals, permission prompts, usage limits, listeners, power and the deadline.
Give standing approvals with a limit in the text, such as "up to 4 submissions
per hour to the practice board until 08:00".

The common ways a night dies: a permission prompt, a usage limit, a listener
nobody restarted, memory running out with two heavy jobs at once.

## One scored problem, no fleet

Skip the overseer. `genie wish`, then one session with `/genie:lab <slug>`. Use
`/loop` or a goal for motion. `genie next` says what to do.

## Adopt a running project

`genie init` changes nothing that exists. For each piece of running work:
`genie track add <slug>`, write its README, run one checkpoint. For scored work,
put past outside scores in `tracks/<slug>/SCORES.md` and run `genie adopt`.

## Find what a compaction dropped

The recording keeps every message and tool result. A compaction does not touch
it.

```bash
genie recall port 8080       # search, newest first. Every word must match
genie recall --at 414684     # one entry in full, the number comes from a hit
genie recall --gap           # everything after the last checkpoint
```

An agent runs these in its own session. From your terminal, name the session
with `--session <id>` or the file with `--file <path>`. Recordings are in
`~/.claude/projects/`.

## See how a track got here

`STATE.md` keeps the last 3 states. `tracks/<slug>/TIMELINE.md` has one row per
checkpoint, oldest first, and is never trimmed. Each row links the findings
that checkpoint filed, so the notes open as a graph in Obsidian. Cross-track
work goes to `notes/timeline.md`.

## Helpers

A helper is a second session or subagent that only reads: labels data, replays
a rival's approach, audits another track's build. Fresh eyes are its value, so
give it the facts and not the theory.

## Spend less

`genie window` measures your sessions and prices every window size. It changes
nothing. Apply a size with `genie on --window <tokens>`, then restart and resume
the sessions. `genie off` removes the limit.

A bigger window means every request reads more from the cache. A smaller one
means more restarts, and each one writes the floor to the cache again. The floor
is the context right after a compaction, not the size of a first hello.

```
trigger = floor + sqrt(2 * growth * (restart + read * floor) / read)
window  = trigger / 0.87
```

Measured on the contest's sessions: floor 103K, growth 1,863 tokens per request.

| Window | Fable 5.2, $ per 1,000 requests | Opus 5.5 |
|---|---|---|
| 150K | 216 | 121 |
| 250K | 132 | 82 |
| 325K | **128** | 84 |
| 500K | 136 | 95 |
| 1M | 177 | 137 |

The curve is flat near its best point, so do not tune by hand. A window close to
the floor is the costly mistake.


- Fewer sessions. Two good tracks beat five thin ones
- Keep the session-start digest and your own CLAUDE.md short, they are paid for
  after every compaction
- Disconnect MCP servers a track does not need
- Never poll with a model. Use `genie watch` and background waits
