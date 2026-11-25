---
name: second-opinion
description: Ask a read-only colleague from another model family. Use when stuck twice on one problem, after 3 rejected ideas, before an action that cannot be undone, before keeping a change that moves the base, or when the human asks for a second opinion.
---

# Second opinion

A fresh view changes the course more often than a third try does.

| Want | Run in the background |
|---|---|
| a colleague that remembers earlier questions | `genie second-opinion ask "question"` |
| a fresh look at one track | `genie second-opinion track <slug> "question"` |
| the newest answer | `genie second-opinion last`, or `last track-<slug>` |

It uses the Codex CLI with a read-only sandbox. `second_opinion` in
`.genie/config.json` sets the model or another program. An answer takes minutes
and spends that provider's quota. The question and the files it reads go to
that provider, so do not use it on work that must stay local.

## Ask well

1. State the goal, what decides it, and the current best.
2. List what was tried, with numbers. Leave out your own theory.
3. Ask for the single largest open gain and the cheapest test of it.

## Use the answer

It is a lead, not a ruling. Check its facts in the files it cites. Send it to
the track as "weigh it, you own the how". Record what you did with it, including
"rejected, because".
