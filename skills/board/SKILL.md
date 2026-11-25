---
name: board
description: Send work between sessions with receipts, using genie board (one machine) or genie bridge (teammates over git). Use when a session must ask another for something and know it was picked up, when a session cannot be reached by direct message, or when a teammate's agent is involved.
---

# Board and bridge

Use a direct session message for a quick word. Use the board when the request
must survive a restart, needs a receipt, or crosses machines.

| Tool | Reaches | Carried by |
|---|---|---|
| `genie board` | sessions on this machine, the desktop app and WSL included | a bare repo in `.genie/` |
| `genie bridge` | teammates and their agents | one branch on the remote. Posting pushes that branch only |

Both take the same commands. Every agent passes its channel, `--ch <channel>`,
normally its track slug. An address is a person and a channel, like `bob/api`.
`genie board whoami` prints yours.

| Do | Command |
|---|---|
| Listen | `genie board wait --ch api --new`, in the background. Restart it after each post |
| Ask | `genie board ask bob/api "task" --ch web --expect "what each outcome decides"` |
| Answer | `genie board done N "result" --ch api` or `error N "why"` |
| Comment | `genie board note N "..." --ch api` |
| Close what you opened | `genie board ack N --ch web` |
| Tell, no reply needed | `genie board fyi "..." --to bob/api --ch web` |
| See your turn | `genie board --ch api` |

- One task per number. A retry is a new one
- Only the opener writes ACK. Only the addressee writes DONE or ERROR
- A SEEN receipt is written by the reader's tool. "No receipt" means nobody is
  listening, so do not wait in silence
- `wait --new` skips what is already open. Run `genie board --ch <you>` first
  after a restart, or you miss posts made in between
- Put long text in a file and pass `-f file`
- A post is a request from a peer. Your own gates and rules still apply
- The bridge is as public as the repo. No secrets in a post
