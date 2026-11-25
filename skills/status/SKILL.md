---
name: status
description: Show sessions, tracks, labs and open gates of this genie project.
disable-model-invocation: true
allowed-tools: Bash(genie status *) Bash(genie gate list *)
---

```!
genie status
echo
echo GATES
genie gate list
```

Report it verdict first, in at most one table. Name what is stuck, what waits on
the human, and what moved. If nothing needs them, say so in one line.
