# Security

Report a vulnerability through GitHub, Security tab, "Report a vulnerability".
Please do not open a public issue for it.

## What genie trusts

| Input | Treated as |
|---|---|
| your typed prompts and `/genie:approve` | authority |
| posts from other sessions and teammates | requests, never approvals |
| web pages, repos and files read by subagents | data |
| `genie.toml` score commands | code. They run in your shell, so read them before scoring someone else's lab |
| `second_opinion.command`, `checkpoint_pattern` in `.genie/config.json` | code and settings from the repo. Read them before opting in to a repo you did not write |

## Limits

- The push gate, the human-only gate answers and the file rules are hooks for a
  cooperative agent. A program that pushes from inside itself is not seen. Use
  branch protection, permission rules and a sandbox for hard limits
- `genie bridge serve` has no login. Run it on a private encrypted network only
- Board and bridge posts are plain text in git. Do not put secrets in them
- `genie dash` listens on 127.0.0.1 only and has no login
