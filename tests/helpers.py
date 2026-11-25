"""Shared test helpers. Every test runs in a temp project with its own home."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
GENIE = SCRIPTS / "genie.py"


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="genie-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.top = self.tmp / "proj"
        self.top.mkdir()
        self.home.mkdir()
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith(("GENIE_", "BRIDGE_", "CLAUDE_", "GIT_"))}
        self.env = {**clean, "HOME": str(self.home), "USERPROFILE": str(self.home),
                    "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Ann Example")
        self.git("config", "user.email", "ann@example.com")

    def git(self, *args, cwd=None):
        p = subprocess.run(["git", *args], cwd=cwd or self.top, capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def g(self, *args, ok=True, cwd=None, env=None, stdin=""):
        p = subprocess.run([sys.executable, str(GENIE), *args], cwd=cwd or self.top, capture_output=True,
                           text=True, encoding="utf-8", env={**self.env, **(env or {})}, input=stdin, timeout=60)
        out = p.stdout + p.stderr
        if ok is not None:
            self.assertEqual(p.returncode == 0, ok, f"{args} -> {p.returncode}\n{out}")
        return out

    def hook(self, event, **fields):
        """Run the hook entry the way Claude Code does. Returns (code, stdout, stderr)."""
        d = {"session_id": "s1", "hook_event_name": event, "cwd": str(self.top), **fields}
        p = subprocess.run([sys.executable, str(GENIE), "hook"], cwd=self.top, capture_output=True, text=True,
                           encoding="utf-8", env={**self.env, "CLAUDE_PROJECT_DIR": str(self.top)},
                           input=json.dumps(d), timeout=30)
        return p.returncode, p.stdout, p.stderr

    def transcript(self, tokens, name="t.jsonl", stamp="2099-01-01T00:00:00.000Z"):
        path = self.tmp / name
        line = {"type": "assistant", "timestamp": stamp, "message": {"usage": {
            "input_tokens": 10, "cache_read_input_tokens": tokens - 20, "cache_creation_input_tokens": 5,
            "output_tokens": 5}}}
        path.write_text(json.dumps(line) + "\n", encoding="utf-8")
        return str(path)

    def state(self, sid="s1"):
        return json.loads((self.home / ".claude" / "genie" / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))
