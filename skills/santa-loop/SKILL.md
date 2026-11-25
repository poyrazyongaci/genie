---
name: santa-loop
description: Two independent reviewers must both pass a change, up to 3 rounds. Use before an outside submission, a final pick, or a code change a score or a release depends on. Never pushes and never submits. Not for drafts or anything a test already settles.
---

# Santa loop

Adapted from `/santa-loop` and `santa-method` in affaan-m/ECC (MIT).

One reviewer misses what its own blind spots hide. Two reviewers with no shared
context catch more, and more still when they come from different model families.

## 1. Scope

Default is the working tree. Write the diff and the request to a file in your
scratch folder, so both reviewers read the same thing.

## 2. Rubric

Every row needs an objective pass condition. Add rows for your track.

| Criterion | Passes when |
|---|---|
| Correctness | it does what the request claims, edge cases handled |
| Prediction | each predicted number traces to a measurement |
| Falsifier | the request says which result would prove it wrong |
| Overfitting | the gain holds on cases it was not tuned on |
| Regressions | nothing that passed before can fail, or the drop is named and priced |
| Output | the artifact passes its own validator |
| Rules | the repo's rules hold, no secrets |

## 3. Two reviewers at once

- **A, another model family.** With the Codex CLI:
  `codex exec --sandbox read-only -o <out> "<rubric, scope file, return JSON>"`
  in the background. No such tool: use a second `genie:reviewer` and mark the
  report "same family, weaker independence".
- **B, a `genie:reviewer` subagent.** Give it the rubric and the scope file.

Both return JSON: `verdict` PASS or FAIL, `checks` (criterion, result, detail),
`critical_issues`, `suggestions`. One failed check makes the verdict FAIL.

## 4. Gate

Both PASS is **NICE**. Anything else is **NAUGHTY**. A reviewer that errors or
times out did not pass.

## 5. Fix and rerun

Fix only what was flagged. Run fresh reviewers that never saw the last round.
After 3 rounds, stop and hand the open issues to the human.

## Report

```
SANTA: NICE | NAUGHTY (escalated)
Reviewer A (<which>): PASS | FAIL
Reviewer B (<which>): PASS | FAIL
Both flagged:
A only:
B only:
Rounds: N/3
```

NICE lets the work reach the next gate. It approves nothing. Weigh what both
flagged above what one flagged. If reviewers pass everything, tighten the rubric.
