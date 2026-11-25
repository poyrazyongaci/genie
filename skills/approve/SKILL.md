---
name: approve
description: Approve a gate. The human types this, agents cannot.
disable-model-invocation: true
argument-hint: "<gate id> [note]"
arguments: [gate, note]
allowed-tools: Bash(genie gate *)
---

The human approved gate $gate. Result:

!`genie gate done "$gate" --answer "yes $note"`

Do exactly the approved action and nothing wider. A `once` approval covers one
action. If the result above shows an error, say so and stop.
