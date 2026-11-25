# Working on this repo

Rules for people and agents who change the kit.

- Standard library Python 3.11 or newer. No dependencies
- `sh bin/genie` is the only entry point. Paths come from `scripts/genie_paths.py`,
  never from `__file__` of a project script
- A hook must never stop a session by accident. It exits 0 on any error, and
  exits 2 only on purpose
- Every hook exits at once in a project without `.genie/config.json`
- Nothing commits, pushes or answers a gate on its own. The one exception is the
  bridge, which pushes its own branch
- A check that guards something needs a test that makes it fire
- Skills stay short. Detail goes to `references/`. Each description says when to
  use the skill
- Subagents get the fewest tools that work
- Claims in the README and docs carry their evidence, or say they have none
- Examples use `ann`, `bob` and `example.com`. No real names, hosts or paths

```bash
python3 -m unittest discover -s tests
sh examples/toy-lab/run.sh
claude plugin validate .
claude --plugin-dir .          # try it in a scratch repo
```
