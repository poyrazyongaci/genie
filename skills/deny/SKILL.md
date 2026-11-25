---
name: deny
description: Deny a gate, or revoke a standing approval. The human types this, agents cannot.
disable-model-invocation: true
argument-hint: "<gate id> [reason]"
arguments: [gate, note]
allowed-tools: Bash(genie gate *)
---

The human denied gate $gate. Result:

!`genie gate done "$gate" --answer "no $note"`

Do not do that action and do not look for a way around it. Carry on with other
work, and change the plan if it depended on this.
