# Changelog

Format: [Keep a Changelog](https://keepachangelog.com). Versions follow semver.

## Unreleased

### Added

- `genie window` measures the floor, growth and cache use of your own sessions
  and prices every compaction window
- `genie recall` reads a session's recording back: a search, one entry in full,
  or everything after the last checkpoint
- After a compaction the reload lists the work between the last checkpoint and
  the messages Claude Code kept, read from the recording
- `tracks/<slug>/TIMELINE.md` and `notes/timeline.md`, one row per checkpoint,
  never trimmed
- A resumed session gets its last state and the recall commands

### Changed

- A checkpoint counts only when it was saved. It leaves a receipt bound to the
  session, and the hooks take that. Before, any command that named
  `genie checkpoint` counted, a `grep` for it included
- After a compaction a session gets its own last state back. Before, it got the
  newest entry in `STATE.md`, which a second session on the track may have
  written
- The checkpoint nudge comes 36K tokens before compaction. It was 72 percent of
  the window, which was too early on big windows and too late on small ones
- After a compaction the nudge waits for 20K tokens of new work

### Fixed

- The field report's share of cache reads
- Two hooks of one session that ran at once could drop each other's state.
  They take turns now, and every temp file has its own name

## 0.1.0

First release as a plugin. Ported from the scripts of a 48 hour contest.

### Added

- `genie` command: init, track, doctor, status, checkpoint, vault, board,
  bridge, watch, dash, second-opinion and the lab commands
- Skills: init, overseer, track, operator, lab, checkpoint, board, scout,
  council, santa-loop, second-opinion, approve, deny, status
- Read-only subagents: reviewer, council-voice, scout-lane
- Hooks: notes digest, checkpoint nudge, compaction hold, reload after
  compaction, push gate

### Changed from the contest scripts

- Opt in per project. Config instead of names in code
- Only the human answers a gate. Standing approvals are honored and revocable
- Labs, gates and the board are shared across git worktrees
- A worker's relative entry path means the file in its own worktree
- The digest is capped
- Two sessions may create the board at the same moment
