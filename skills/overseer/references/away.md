# Before the human steps away

Run this list and show it as one table. A row that fails is their decision
before they go.

| Check | How |
|---|---|
| every track has a next step that does not wait on them | `genie status`, and ask the tracks that wait |
| gates | `genie gate list`. Ask now for the standing approvals you will need, each with a limit |
| permission prompts | each session can run its usual commands without asking. A prompt stops it until they return |
| usage limits | which model each session uses and when its limit resets. A limited session looks idle |
| listeners and watcher | `genie watch --alive`, one board wait running |
| outside route | one harmless round trip through the operator |
| what they kept | final picks, spending, anything public. Get it in their own words |
| machine | power, sleep setting, memory, free disk. One heavy job at a time |
| deadline | the last time to submit, and an earlier time after which nothing new is tried |

## While they are away

- Nothing they kept is yours to decide. Hold it, and prepare both answers
- A gate that times out is a no
- If a session restarts, send it its standing rules again
- After a machine restart: name the sessions that came back, resend listeners,
  check that temporary folders survived

## When they return

One table: what improved, what did not, what waits on them, what broke and is
fixed. Then your rulings since they left, with the rejected options.
