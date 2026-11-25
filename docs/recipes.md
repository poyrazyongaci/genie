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

## Helpers

A helper is a second session or subagent that only reads: labels data, replays
a rival's approach, audits another track's build. Fresh eyes are its value, so
give it the facts and not the theory.

## Spend less

- Fewer sessions. Two good tracks beat five thin ones
- Keep the session-start digest and your own CLAUDE.md short, they are paid for
  after every compaction
- Disconnect MCP servers a track does not need
- Never poll with a model. Use `genie watch` and background waits
