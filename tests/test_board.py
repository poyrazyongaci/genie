"""Board and bridge: one request from ask to ack, with receipts and permissions."""
import subprocess
import sys

from helpers import GENIE, ProjectTest


class BoardTest(ProjectTest):
    def setUp(self):
        super().setUp()
        self.g("init", "--no-window")

    def test_round_trip(self):
        p = subprocess.Popen([sys.executable, str(GENIE), "board", "wait", "--ch", "operator", "--timeout", "40"],
                             cwd=self.top, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(p.kill)
        out = self.g("board", "ask", "me/operator", "SUBMIT api abc123", "--ch", "overseer",
                     "--expect", "above 5 keeps it", "--confirm", "30")
        self.assertIn("seen by ann/operator", out)
        got, _ = p.communicate(timeout=40)
        self.assertIn("SUBMIT api abc123", got)
        self.assertIn("above 5 keeps it", got)
        self.assertIn("Only the addressee", self.g("board", "done", "1", "x", "--ch", "api", ok=False))
        self.g("board", "done", "1", "5.2, rank 3", "--ch", "operator")
        self.assertIn("5.2, rank 3", self.g("board", "wait", "--ch", "overseer", "--timeout", "5"))
        self.g("board", "ack", "1", "--ch", "operator", ok=False)
        self.g("board", "ack", "1", "--ch", "overseer")
        self.assertIn("ACK", self.g("board", "log"))
        self.assertIn("nothing waiting on you", self.g("board", "brief", "--ch", "overseer"))

    def test_many_sessions_may_start_the_board_at_once(self):
        ps = [subprocess.Popen([sys.executable, str(GENIE), "board", "fyi", f"hello {n}", "--to", "me/hub",
                                "--ch", f"t{n}"], cwd=self.top, env=self.env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True) for n in range(6)]
        for p in ps:
            out, _ = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, out)
        log = self.g("board", "log")
        for n in range(6):
            self.assertIn(f"hello {n}", log)

    def test_channels_are_exact(self):
        self.g("board", "fyi", "for uploads only", "--to", "me/web/upload", "--ch", "api")
        self.g("board", "ask", "me/web/upload", "do it", "--ch", "api", "--confirm", "0")
        self.assertIn("nothing waiting", self.g("board", "brief", "--ch", "web"))
        self.assertIn("1 waiting on you", self.g("board", "brief", "--ch", "web/upload"))

    def test_no_listener_is_said_out_loud(self):
        self.assertIn("no receipt within", self.g("board", "ask", "me/operator", "x", "--ch", "api", "--confirm", "1"))

    def test_board_stays_out_of_the_checkout(self):
        self.g("board", "fyi", "hello", "--to", "me/api", "--ch", "web")
        self.assertNotIn("BRIDGE.md", self.git("status", "--porcelain", "-uall"))
        self.assertNotIn("board.git", self.git("status", "--porcelain", "-uall"))

    def test_bridge_pushes_one_branch(self):
        remote = self.tmp / "remote.git"
        self.git("init", "-q", "--bare", str(remote))
        self.git("remote", "add", "origin", str(remote))
        self.g("bridge", "fyi", "hello", "--to", "bob", "--ch", "api")
        self.assertEqual(self.git("ls-remote", "--heads", "origin").split()[1:], ["refs/heads/genie-bridge"])
        bob = {"BRIDGE_ME": "bob"}
        self.g("bridge", "ask", "bob/api", "review it", "--ch", "api", "--confirm", "0")
        self.assertIn("review it", self.g("bridge", "wait", "--ch", "api", "--timeout", "5", env=bob))
        self.g("bridge", "error", "2", "no time", "--ch", "api", env=bob)
        self.assertIn("no time", self.g("bridge", "wait", "--ch", "api", "--timeout", "5"))
