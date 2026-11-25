"""The watcher's detectors, each forced to fire, and the dashboard's data."""
import json
import os
import subprocess
import sys
import time

from helpers import GENIE, SCRIPTS, ProjectTest


class WatchTest(ProjectTest):
    def setUp(self):
        super().setUp()
        self.g("init", "--no-window")
        self.set_watch(sessions=["overseer"])

    def set_watch(self, **kw):
        cfg = self.top / ".genie" / "config.json"
        d = json.loads(cfg.read_text())
        d["watch"].update(kw)
        cfg.write_text(json.dumps(d))

    def session(self, name, status, sid="s1", pid=None):
        d = self.home / ".claude" / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.json").write_text(json.dumps(
            {"pid": pid or os.getpid(), "name": name, "status": status, "sessionId": sid}))

    def say(self, text, sid="s1", stamp="2026-01-01T00:00:00.000Z"):
        d = self.home / ".claude" / "projects" / "x"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sid}.jsonl").write_text(json.dumps(
            {"type": "assistant", "timestamp": stamp, "message": {"content": [{"type": "text", "text": text}]}}) + "\n")

    def once(self, *a):
        return self.g("watch", "--once", *a)

    def test_quiet_when_well(self):
        self.session("overseer", "busy")
        self.assertIn("0 alert(s)", self.once("--now"))
        self.assertIn("DEAD", self.g("watch", "--alive", env={"GENIE_HOME": str(self.tmp / "elsewhere")}, ok=False))

    def test_gone(self):
        self.assertIn("GONE overseer", self.once("--now"))
        self.assertIn("0 alert(s)", self.once())  # not before the threshold

    def test_limit_is_not_a_stall(self):
        self.session("overseer", "idle")
        self.say("You've hit your usage limit, resets 10am")
        out = self.once("--now")
        self.assertIn("LIMIT overseer", out)
        self.assertNotIn("STALL", out)

    def test_stall_needs_an_open_lab_job(self):
        self.g("track", "add", "api")
        self.session("api", "idle", sid="s2")
        self.session("overseer", "busy", sid="s1")
        self.say("done for now", sid="s2")
        d = self.home / ".claude" / "genie" / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        (d / "s2.json").write_text(json.dumps({"track": "api"}))
        self.assertNotIn("STALL", self.once("--now"))
        self.g("wish", "go", "--track", "api", "--better", "lower")
        self.assertIn("STALL api", self.once("--now"))
        self.session("api", "busy", sid="s2")
        self.assertNotIn("STALL", self.once("--now"))

    def test_hub_turn_and_listener(self):
        if os.name == "nt":
            self.skipTest("needs ps")
        self.set_watch(listeners=["board"])
        self.session("overseer", "idle")
        self.say("ok")
        self.g("board", "ask", "me/overseer", "submit it", "--ch", "api", "--confirm", "0")
        out = self.once("--now")
        self.assertIn("HUB E1 on the board", out)
        self.assertIn("LISTENER", out)

    def test_loop_reports_once_and_exits(self):
        env = {**self.env}
        self.set_watch(gone_minutes=0)
        p = subprocess.run([sys.executable, str(GENIE), "watch"], cwd=self.top, env=env, capture_output=True,
                           text=True, timeout=30)
        self.assertIn("GONE overseer", p.stdout)
        q = subprocess.Popen([sys.executable, str(GENIE), "watch"], cwd=self.top, env=env, stdout=subprocess.PIPE,
                             text=True)
        self.addCleanup(q.kill)
        time.sleep(2.5)
        self.assertIsNone(q.poll())  # the same problem is not reported again
        self.assertIn("alive", self.g("watch", "--alive"))
        q.kill()
        q.communicate()

    def test_dash_data(self):
        self.g("track", "add", "api", "The API", "--owner", "ann")
        self.g("gate", "add", "push it", "--kind", "push")
        self.g("board", "ask", "me/overseer", "look", "--ch", "api", "--confirm", "0")
        code = ("import sys, json; sys.path.insert(0, sys.argv[1]); import genie_dash as d; "
                "print(json.dumps(d.snapshot()))")
        p = subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], cwd=self.top, env=self.env,
                           capture_output=True, text=True, timeout=60)
        data = json.loads(p.stdout)
        self.assertEqual(data["status"][0]["track"], "The API")
        self.assertEqual(data["gates"][0]["id"], "G1")
        self.assertEqual(data["open"][0]["where"], "board")
        self.assertIn("sessions", data["genie"])

    def test_second_opinion_custom_program(self):
        self.g("track", "add", "api")
        cfg = self.top / ".genie" / "config.json"
        d = json.loads(cfg.read_text())
        d["second_opinion"] = {"command": [sys.executable, "-c", (
            "import sys; open(sys.argv[1], 'w').write('Verdict: no.\\nTRACK api: look at the cache\\n')"), "{out}"]}
        cfg.write_text(json.dumps(d))
        out = self.g("second-opinion", "ask", "is it enough?")
        self.assertIn("colleague:", out)
        self.assertIn("track api:", out)
        self.assertIn("Verdict: no.", self.g("second-opinion", "last"))
        d["second_opinion"] = {"command": ["no-such-program-here"]}
        cfg.write_text(json.dumps(d))
        self.assertIn("FAILED", self.g("second-opinion", "ask", "x", ok=False))
