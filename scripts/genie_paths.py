"""Where things live. Shared by every genie script. Standard library only.

Two roots matter:

  checkout()  the working tree you are in. Notes and STATE.md belong to it.
  root()      the main working tree. Labs, gates, locks and the local board
              live there, so sessions in linked worktrees share one copy.

Both are the same folder unless you are in a linked git worktree.
GENIE_ROOT overrides root(). Nothing here runs git, because hooks call it
on every tool call.
"""
import json
import os
import re
from pathlib import Path

CONFIG = Path(".genie") / "config.json"
HOME = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())
# Session state is per machine, not per project, and survives plugin updates.
STATE_HOME = Path(os.environ.get("GENIE_HOME") or HOME / ".claude" / "genie")

COMPACT_RATIO = 0.87   # Claude Code compacts at this share of the window (measured, undocumented)

DEFAULTS = {
    "version": 1,
    # Claude Code compacts at about 87 percent of this many tokens.
    "compact_window": 250_000,
    "hooks": {"keep_alive": True, "digest": True, "bridge": False, "rules": True},
    "notes": "notes",
    "hub": "overview",           # the hub note, notes/<hub>.md
    "digest_chars": 6_000,       # session start output. It is paid for after every compaction
    "checkpoint_pattern": "",    # extra regex for a command that counts as a checkpoint
    "bridge": {"branch": "genie-bridge", "remote": "origin"},
    "watch": {
        "sessions": [],          # session names that must stay alive
        "hub_session": "overseer",
        "hub_channel": "overseer",
        "listeners": [],         # "board" and/or "bridge" when the hub must be listening
        "stall_minutes": 10,
        "gone_minutes": 5,
        "listener_minutes": 5,
        "turn_minutes": 5,
        "dash": False,
    },
    "dash": {"port": 8799, "title": "", "deadline": ""},
    "second_opinion": {"model": "", "track_model": "", "effort": "high", "timeout": 1200, "command": []},
}


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _start(start=None):
    return Path(start or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def checkout(start=None):
    """Nearest folder upward that holds .genie/config.json, else the git top level, else start."""
    here = _start(start)
    top = None
    for d in (here, *here.parents):
        if (d / CONFIG).is_file():
            return d
        if top is None and (d / ".git").exists():
            top = d
    return top or here


def main_tree(top):
    """The main working tree when `top` is a linked git worktree, else `top`."""
    dotgit = Path(top) / ".git"
    if not dotgit.is_file():
        return Path(top)
    try:
        m = re.match(r"gitdir:\s*(.+)", dotgit.read_text(encoding="utf-8").strip())
        gitdir = Path(m.group(1))
        gitdir = (gitdir if gitdir.is_absolute() else Path(top) / gitdir).resolve()
        # <main>/.git/worktrees/<name>
        if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
            main = gitdir.parent.parent.parent
            if main.is_dir():
                return main
    except (OSError, AttributeError):
        pass
    return Path(top)


def root(start=None):
    env = os.environ.get("GENIE_ROOT")
    if env:
        return Path(env).resolve()
    top = checkout(start)
    main = main_tree(top)
    return main if (main / CONFIG).is_file() or not (top / CONFIG).is_file() else top


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def config(start=None):
    cfg = read_json(root(start) / CONFIG, None)
    return _merge(DEFAULTS, cfg if isinstance(cfg, dict) else {})


def enabled(start=None):
    return (root(start) / CONFIG).is_file()


def hub_note(top=None):
    cfg = config()
    return Path(top or checkout()) / cfg["notes"] / f"{cfg['hub']}.md"
