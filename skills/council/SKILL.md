---
name: council
description: Four independent voices argue a go or no-go call or a fork with several credible paths before anyone picks. Use for a risky or irreversible choice, when you suspect you are anchored on your own plan, or when the human asks for dissent. Not for checking whether output is correct (use santa-loop) and not for plain facts.
---

# Council

Adapted from the `council` skill in affaan-m/ECC (MIT).

A session that worked for hours anchors on its own plan. Three fresh subagents
see the question and the facts, never the conversation. Their disagreement is
the product, not a vote.

1. **Question.** One line: what is decided, under which constraints, what counts
   as a win. Name the deadline.
2. **Facts.** Numbers, limits, time left. Under 40 lines, without your reading
   of them.
3. **Your position first.** Write it down with three reasons and its biggest
   risk before any voice returns.
4. **Three voices in one message**, each a `genie:council-voice` subagent with
   the question, the facts and its role:

   | Voice | Lens |
   |---|---|
   | You, Architect | correctness, what holds on unseen cases |
   | Skeptic | challenge the question, find the simplest credible alternative |
   | Pragmatist | time left, attempts left, what ships |
   | Critic | failure modes, overfitting, the cost of being wrong |

5. **Synthesis.** Show all four positions before the verdict. Say which voice
   changed your mind. Two voices against you is a real signal. Always report
   the strongest dissent.

```markdown
## Council: <title>

| Voice | Position | Why |
|---|---|---|

- **Consensus:**
- **Strongest dissent:**
- **Premise check:** did the Skeptic reject the question?
- **Recommendation:**
```

The human decides. If it changes a track, file one decision with
`genie:checkpoint`. One round by default. Do not run it for a choice the
scoring rule already settles.
