#!/usr/bin/env python3
"""Local board. The bridge, pointed at a bare repo inside this project.

Sessions on one machine (terminals, the desktop app, WSL and Windows on one
checkout) share it, so their posts need no network and no remote. Same
commands, channels and receipts as `genie bridge`.

    genie board wait --ch operator
    genie board ask me/operator "SUBMIT api ..." --ch overseer --expect "..."
    genie board done 3 "result" --ch operator

The board is .genie/board.git, which git ignores. Nothing here reaches another
machine. Use `genie bridge` for that.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genie_paths  # noqa: E402

NAME = "genie-board"
URL = ".genie/board.git"  # relative, so Windows and WSL both resolve it
BRANCH = "genie-bridge"


def git(*args):
    return subprocess.run(["git", "-C", str(genie_paths.root()), *args], capture_output=True)


def ensure():
    """Make the board once. Two sessions often start at the same moment, so the
    repo is built aside and renamed in, and a busy git config is retried."""
    root = genie_paths.root()
    repo = root / URL
    if not (repo / "HEAD").exists():
        repo.parent.mkdir(exist_ok=True)
        aside = Path(tempfile.mkdtemp(prefix="board-", suffix=".tmp", dir=repo.parent))
        try:
            subprocess.run(["git", "init", "-q", "--bare", "--template=", str(aside)], check=True)
            os.rename(aside, repo)
        except OSError:  # another session was first
            if not (repo / "HEAD").exists():
                raise
        finally:
            shutil.rmtree(aside, ignore_errors=True)
    want = {"url": URL,
            # keep a plain `git fetch --all` or `git push` away from the board
            "skipFetchAll": "true",
            "fetch": f"+refs/heads/{BRANCH}:refs/remotes/{NAME}/{BRANCH}"}
    for key, value in want.items():
        for _ in range(20):
            if git("config", "--get", f"remote.{NAME}.{key}").returncode == 0:
                break
            git("config", f"remote.{NAME}.{key}", value)
            time.sleep(0.05)


def main(argv=None):
    if not (genie_paths.root() / ".git").exists():
        print("genie board needs a git repository", file=sys.stderr)
        return 1
    ensure()
    os.environ.update(BRIDGE_REMOTE=NAME, BRIDGE_BRANCH=BRANCH, BRIDGE_LAN="off", BRIDGE_PROG="genie board")
    os.environ.setdefault("BRIDGE_INTERVAL", "3")
    import bridge
    return bridge.main(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
