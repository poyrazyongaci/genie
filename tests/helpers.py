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


class Recording:
    """A stand-in for a Claude Code transcript, with the fields genie reads."""

    def __init__(self, path, tokens=120_000):
        self.path, self.tokens, self.rows, self.n = Path(path), tokens, [], 0

    def _add(self, row, at=None):
        self.n += 1
        row.setdefault("uuid", f"u{self.n}")
        row["timestamp"] = at or "2099-01-01T%02d:%02d:%02d.000Z" % (self.n // 3600, self.n // 60 % 60, self.n % 60)
        self.rows.append(row)
        return row["uuid"]

    def _usage(self):
        return {"input_tokens": 10, "cache_read_input_tokens": self.tokens - 20,
                "cache_creation_input_tokens": 5, "output_tokens": 5}

    def say(self, text, at=None, **extra):
        """A message typed by the human or sent by another agent."""
        return self._add({"type": "user", "message": {"role": "user", "content": text}, **extra}, at)

    def note(self, text, at=None):
        """Text from the agent itself."""
        return self._add({"type": "assistant", "message": {
            "role": "assistant", "usage": self._usage(), "content": [{"type": "text", "text": text}]}}, at)

    def call(self, name, tool_input, result="ok", at=None, side=False):
        """A tool call and its result. Returns (tool_use id, uuid of the call entry)."""
        tid = f"toolu_{self.n + 1}"
        uuid = self._add({"type": "assistant", "isSidechain": side, "message": {
            "role": "assistant", "usage": self._usage(),
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": tool_input}]}}, at)
        self._add({"type": "user", "isSidechain": side, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result}]}}, at)
        return tid, uuid

    def boundary(self, head=None, at=None):
        """Claude Code compacts here and keeps everything from `head` on, word for word."""
        meta = {"trigger": "auto", "preTokens": self.tokens}
        if head:
            meta["preservedSegment"] = {"headUuid": head, "tailUuid": self.rows[-1]["uuid"]}
        self._add({"type": "system", "subtype": "compact_boundary", "compactMetadata": meta}, at)
        self._add({"type": "user", "isCompactSummary": True, "message": {
            "role": "user", "content": "This session is being continued from a previous conversation."}}, at)

    def write(self):
        self.path.write_text("".join(json.dumps(r) + "\n" for r in self.rows), encoding="utf-8")
        return str(self.path)
