# Troubleshooting

Start with `genie doctor`.

| Symptom | Cause | Fix |
|---|---|---|
| hooks do nothing | the project has no `.genie/config.json`, or the worktree lacks it | `genie init`, and commit `.genie` |
| `genie: command not found` in your terminal | the command is on Claude's Bash path, not yours | run `! genie status` in a session, or link `bin/genie` into your PATH |
| the window did not change | `autoCompactWindow` applies to sessions started or resumed later | restart and resume |
| sessions compact every few calls | the window is close to the floor | run `genie window`, raise it or cut the floor |
| hooks changed, a running session does not notice | a terminal session keeps the hooks it read at startup | restart and resume |
| a compaction came with no checkpoint | context was past 96 percent, or it was manual | checkpoint big sessions before resuming them |
| a session is held again and again | it makes no tool call that reaches the hook, or its checkpoint is refused | run the checkpoint by hand and read the refusal |
| "waiting" sessions pile up | a permission prompt upstream | look for the session that waits on you |
| a session is idle and quiet | a usage limit. No nudge can move it | the watcher says LIMIT. Switch its model |
| "no receipt within 15 s" | nobody listens on that channel | start its `genie board wait` |
| a post was missed after a restart | `wait --new` skips what is already open | run `genie board --ch <you>` first |
| the watcher does not see sessions | Claude Code's session files moved or changed | `genie watch` skips what it cannot read. Please open an issue |
| push refused although you approved | `once` was used up, even if the push failed | approve again, or ask for a standing gate |
| score refused, "the scorer changed" | a scorer file differs from the freeze | restore it, or `genie golden` and `genie freeze` |
| Windows: hook errors | no Git Bash, or Python is the Store stub | install Git for Windows and Python 3.11 |

`~/.claude/genie/precompact.log` has one line per compaction request with the
context size, the checkpoint's age and what was decided.

Turn it off for a project: `genie off`, or delete `.genie/config.json`. Remove it
everywhere: `claude plugin uninstall genie@genie`.
