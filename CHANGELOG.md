# Changelog

Format: [Keep a Changelog](https://keepachangelog.com). Versions follow semver.

## Unreleased

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
