---
name: init
description: Set up this project for a genie fleet. Scaffolds .genie, the shared notes and the first tracks, and explains how to open the sessions.
disable-model-invocation: true
argument-hint: "[what you want the fleet to do]"
allowed-tools: Bash(genie doctor *) Bash(genie status *) Bash(genie track list *)
---

# Set up a fleet

The human wants: $ARGUMENTS

1. Run `genie doctor`. Fix a FAIL other than "project opted in" first.
2. Say what setup will change, then ask for a yes:
   - `.genie/` (config, gate queue, local board)
   - `notes/` (hub note, decisions, findings) and `tracks/`
   - `autoCompactWindow` in `.claude/settings.local.json`, unless they pass
     on it (`--no-window`). It makes this project's sessions compact earlier
   - hooks start to work in this project: digest, checkpoint nudge, push gate
3. Run `genie init`. It never overwrites a file.
4. Split the work into tracks. A track is one piece with one owner and its own
   way to be judged. Two or three is a good start, and each costs a full
   session's tokens. Propose them, then run `genie track add <slug> "<title>"`
   for each. Add `genie track add overseer "Overseer desk"` so the hub has a
   STATE.md too.
5. Fill each `tracks/<slug>/README.md`: goal, how it is judged, limits. Ask the
   human only what the repo does not answer.
6. Add `.claude/settings.local.json` to `.gitignore` if it is missing.
   Show the files to commit. Commit only if they say so.
7. Print the commands to open the fleet, one terminal each:

   ```
   claude --name overseer      then type  /genie:overseer
   claude --name <slug>        then type  /genie:track <slug>
   ```

   Add `claude --name operator` with `/genie:operator` only when something
   outside the repo has to be touched.

Tell them: talk to the overseer, not to the tracks. Answer gates with
`/genie:approve <id>`.
