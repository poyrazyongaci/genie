# Architecture

Three layers, one job each.

| Layer | Job | Where |
|---|---|---|
| Scripts | facts and bookkeeping, no judgment | `scripts/`, run as `genie` |
| Skills and subagents | judgment | `skills/`, `agents/` |
| Hooks | rules that must not depend on memory | `hooks/hooks.json` |

Standard library Python only. No services, no database.

## Files in your project

```
.genie/
  config.json        opt-in switch and settings. Commit it
  GATES.md           project gate queue
  board.git/         local board, ignored by git
  second-opinion/    answers, ignored by git
notes/
  overview.md        hub note with the status board
  decisions.md       one row per decision
  findings/          one note per finding
tracks/<slug>/
  README.md          goal, how it is judged, limits
  STATE.md           last 3 checkpoints, newest first
  SCORES.md          one row per outside attempt
  lab/               only for scored work
    WISH.md          problem card
    genie.toml       score commands per tier, scorer and solution paths
    BACKLOG.md       ideas with rank, first test, files, status
    ledger.tsv       one row per scored attempt, append only
    GATES.md         gates of this track
    state.json       measured facts: noise, scorer hash, clocks
    briefs/ golden/ logs/
```

In a linked git worktree, notes and `STATE.md` belong to that worktree. Labs,
gates, locks and the board belong to the main one, so every session shares them.

## Hooks

| Event | Does | Can block |
|---|---|---|
| SessionStart | prints the notes digest, lab lines and open gates. After a compaction it gives back the track's newest checkpoint | no |
| PostToolUse | reads context size from the transcript, asks for a checkpoint past 72 percent | no |
| PreCompact | holds automatic compaction until a fresh checkpoint exists | yes, until 96 percent or 25 holds |
| PreToolUse | push gate, human-only gate answers, record files, solver and scorer split | yes |

A checkpoint is fresh when it is under 30 minutes and under 10K tokens old. One
bound alone is not enough. A manual `/compact` is never held. A hook that fails
exits 0.

Output at session start is capped (`digest_chars`), because every token there is
paid again after each compaction.

## Config

`.genie/config.json`. Every key is optional.

| Key | Default | Meaning |
|---|---|---|
| `compact_window` | 250000 | tokens. Compaction starts near 87 percent of it |
| `hooks.keep_alive` | true | nudge, hold and reload |
| `hooks.digest` | true | notes digest at session start |
| `hooks.bridge` | false | bridge line at session start, reads the remote |
| `hooks.rules` | true | push gate and the other PreToolUse rules |
| `notes`, `hub` | `notes`, `overview` | notes folder and hub note |
| `digest_chars` | 6000 | digest size |
| `checkpoint_pattern` | empty | regex for a command of yours that counts as a checkpoint |
| `bridge.remote`, `bridge.branch` | `origin`, `genie-bridge` | where the bridge lives |
| `watch.sessions` | [] | session names that must stay alive |
| `watch.listeners` | [] | `board` and or `bridge`, when the hub must listen |
| `watch.hub_session`, `watch.hub_channel` | `overseer` | the hub's name and channel |
| `watch.stall_minutes` and the other `_minutes` | 10, 5, 5, 5 | thresholds |
| `watch.dash` | false | alert when the dashboard is down |
| `dash.port`, `dash.title`, `dash.deadline` | 8799, empty | deadline is an ISO time |
| `second_opinion.model`, `.track_model`, `.effort`, `.timeout`, `.command` | empty, high, 1200 | see the skill |

Choosing the window: every call re-reads the context, so a big window costs
more per call. Each compaction costs the base that is loaded again. The usable
room is the window minus that base, so a small window compacts constantly.
Measure your base before lowering it. A small window
also leaves little room between the hold and the ceiling. At 100K one large file
read can jump across it. 200K or more is a better floor.

## Board and bridge protocol

One markdown file on its own git branch, numbered epochs, one state per epoch.

```
READY -> SEEN -> DONE or ERROR -> ACK          NOTE and FYI at any time
```

Only the opener writes READY and ACK. Only the addressee writes DONE and ERROR.
SEEN is written by the reader's tool on delivery. Posts that race are rebuilt
on top of each other, never lost. `genie bridge serve` runs an optional relay
for live delivery between two machines. Run it on a private encrypted network
only, since it has no login of its own.

## Gates

| Status | Means |
|---|---|
| open | waits on the human |
| approved | one action allowed, becomes used when taken |
| standing | allowed until revoked |
| denied, revoked, used | closed |

`/genie:approve` and `/genie:deny` are skills the model cannot invoke. The
PreToolUse rule refuses `genie gate done` from a tool call. An attempt that
fails still uses a `once` approval.
