---
name: scout
description: Time-boxed prior-art search before building, with parallel read-only lanes and one ranked "try first" list. Use before code on a new problem, when a loop stalls (3 rejected ideas in a row or an hour with no gain), or when the human says scout, research or prior art.
---

# Scout

Someone has usually solved a problem close to yours. The risk is that research
eats the first hour, so the time box is a hard rule. Scout writes notes, never
solution code.

| Phase | Budget |
|---|---|
| look at home, write the card | 5 min |
| lanes, in parallel | 15 min |
| merge and file | 10 min, then stop |

## 1. Look at home

Grep `notes/` and `tracks/<slug>/` for the problem's key nouns. Extend an older
scout note instead of redoing it.

## 2. Problem card

Every lane gets it word for word.

```
Problem: one paragraph in plain words, nothing private
Judged by: the exact rule, the unit, which direction is better
Limits: runtime, hardware, language, rules on outside code
We already know: 3 to 6 bullets with the current best
Question: what this scout must answer
Search terms: 5 to 10, with the names other fields use for this problem
```

The last line matters most. A CI test selector is "regression test selection"
in papers.

## 3. Lanes

Send the ones that fit in one message, each as a `genie:scout-lane` subagent.
Give each the card, its lane name and a budget of 15 minutes or 25 tool calls.

| Lane | Looks for |
|---|---|
| rivals | public entries for this exact task |
| repos | open source for the same class of problem |
| papers | methods, benchmarks, surveys |
| contests | winning writeups from similar contests |
| primitives | the fastest known building block for the hot path |

## 4. Merge

Lane reports are leads, not proof. Drop duplicates and what the limits rule
out. Open the source of your top 3 yourself and check what the number measured.
Check the license and the rules before planning to reuse code. Rank at most 5:

| # | Idea | Source | Expected gain | Cost | Risk | First test |
|---|---|---|---|---|---|---|

An idea with no first test is not on the list.

## 5. File it

One `genie:checkpoint` call: a finding `<slug>-prior-art`, status draft, with
the question, the table, sources with dates and the dead ends. In a lab, also
`genie idea add "<idea>" --test "<first test>" --files "<paths>" --track <slug>`.
Then start on idea 1. Do not wait for the human unless it needs a gate.

On a stall, run 1 or 2 lanes for 10 minutes on the wall itself, and tell them
what was already tried.
