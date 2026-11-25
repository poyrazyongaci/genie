"""Set up, notes, checkpoint and path resolution."""
import json
import os
import subprocess
import sys

from helpers import GENIE, SCRIPTS, ProjectTest

ENTRY = """## Goal
Make it fast.

## Next
Try a cache.
===DURABLE===
>>> finding api-batch-size | 64 beats 16 | status: confirmed
## What happened
41 s against 86 s.
>>> decision Use 64 | faster. Rejected: 256 | ann
>>> board api | local: 41 s | next: cache | owner: ann
>>> score 41 s | 44 s | a1b2c3d | batch 64
"""


class NotesTest(ProjectTest):
    def test_init_is_idempotent_and_never_overwrites(self):
        self.g("init", "--no-window")
        hub = self.top / "notes" / "overview.md"
        hub.write_text(hub.read_text() + "\nmine\n")
        self.assertIn("already set up", self.g("init", "--no-window"))
        self.assertIn("mine", hub.read_text())
        self.assertFalse((self.top / ".claude").exists())
        self.assertIn("all required checks pass", self.g("doctor"))

    def test_commands_say_what_to_do_before_init(self):
        for cmd in (["status"], ["vault", "index"], ["watch", "--once"], ["dash"], ["track", "add", "x"],
                    ["second-opinion", "last"], ["checkpoint", "--track", "none", "x"]):
            self.assertIn("genie init", self.g(*cmd, ok=False), cmd)

    def test_track_rows(self):
        self.g("init")
        self.g("track", "add", "api", "The | API")
        self.g("track", "add", "api")
        self.g("track", "add", "Bad Slug", ok=False)
        hub = (self.top / "notes" / "overview.md").read_text()
        self.assertEqual(hub.count("tracks/api/README"), 1)
        self.assertIn("0 errors", self.g("vault", "check"))

    def test_checkpoint_files_everything(self):
        self.g("init")
        self.g("track", "add", "api", "The API")
        out = self.g("checkpoint", "--track", "api", "batch", stdin=ENTRY)
        self.assertIn("0 errors", out)
        self.assertIn("git add --", out)
        hub = (self.top / "notes" / "overview.md").read_text()
        self.assertIn("41 s", hub)
        self.assertIn("[[api-batch-size]]", hub)
        self.assertIn("Rejected: 256", (self.top / "notes" / "decisions.md").read_text())
        self.assertIn("a1b2c3d", (self.top / "tracks" / "api" / "SCORES.md").read_text())
        dig = self.g("vault", "index")
        for part in ("Try a cache.", "api-batch-size", "Use 64", "41 s"):
            self.assertIn(part, dig)
        for n in range(4):
            self.g("checkpoint", "--track", "api", f"t{n}", stdin=ENTRY)
        state = (self.top / "tracks" / "api" / "STATE.md").read_text()
        self.assertEqual(state.count("<!-- checkpoint -->"), 3)
        self.assertIn("## Updates", (self.top / "notes" / "findings" / "api-batch-size.md").read_text())
        self.assertEqual(self.git("log", "--oneline", "--all"), "")  # it never commits

    def test_checkpoint_refuses_before_writing(self):
        self.g("init")
        self.g("track", "add", "api")
        before = sorted(p.as_posix() for p in self.top.rglob("*") if p.is_file() and ".git" not in p.parts)
        for bad in ("## Goal\nno durable part\n", "x\n===DURABLE===\n", "x\n===DURABLE===\n>>> nonsense y\n",
                    "x\n===DURABLE===\n>>> finding Bad_Slug | s | status: draft\nb\n",
                    "x\n===DURABLE===\n>>> board nothere | local: 1\n",
                    "x\n===DURABLE===\n>>> append ../../etc/hosts.md#H\nx\n"):
            self.assertIn("refusing", self.g("checkpoint", "--track", "api", "t", stdin=bad, ok=False))
        self.g("checkpoint", "--track", "ghost", "t", stdin=ENTRY, ok=False)
        self.g("checkpoint", "--track", "../x", "t", stdin=ENTRY, ok=False)
        after = sorted(p.as_posix() for p in self.top.rglob("*") if p.is_file() and ".git" not in p.parts)
        self.assertEqual(before, after)

    def test_digest_is_capped(self):
        self.g("init")
        fdir = self.top / "notes" / "findings"
        for n in range(200):
            (fdir / f"f-{n:03d}.md").write_text(
                f"---\ndate: 2026-01-01\nupdated: 2026-01-{n % 28 + 1:02d}\ntags: [finding]\ntype: finding\n"
                f"status: draft\nsummary: {'long words ' * 12}\n---\nBack to [[overview]].\n", encoding="utf-8")
        dig = self.g("vault", "index")
        self.assertLessEqual(len(dig), 6_100)
        self.assertIn("older ones", dig)
        self.assertIn("2026", (fdir / "f-000.md").read_text())
        self.assertGreater(len(self.g("vault", "index", "--all")), 20_000)
        code, out, _ = self.hook("SessionStart", source="startup")
        self.assertEqual(code, 0)
        self.assertLess(len(out), 10_000)

    def test_check_finds_problems(self):
        self.g("init")
        (self.top / "notes" / "findings" / "Bad Name.md").write_text("no frontmatter [[missing]]\n")
        out = self.g("vault", "check", ok=False)
        for part in ("broken link [[missing]]", "kebab-case", "missing YAML frontmatter"):
            self.assertIn(part, out)

    def test_worktrees_share_the_lab_but_not_the_notes(self):
        self.g("init")
        self.g("track", "add", "api")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "setup")
        wt = self.tmp / "wt"
        self.git("worktree", "add", "-q", str(wt), "-b", "idea")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import genie_paths as p; "
                "print(p.root()); print(p.checkout())")
        out = subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], cwd=wt, capture_output=True, text=True,
                             env=self.env).stdout.split("\n")
        self.assertEqual(os.path.realpath(out[0]), os.path.realpath(self.top))
        self.assertEqual(os.path.realpath(out[1]), os.path.realpath(wt))
        self.g("wish", "go", "--track", "api", "--better", "lower", cwd=wt)
        self.assertTrue((self.top / "tracks" / "api" / "lab" / "genie.toml").exists())
        self.assertFalse((wt / "tracks" / "api" / "lab").exists())
        self.g("checkpoint", "--track", "api", "t", stdin=ENTRY, cwd=wt)
        self.assertTrue((wt / "tracks" / "api" / "STATE.md").exists())
        self.assertFalse((self.top / "tracks" / "api" / "STATE.md").exists())

    def test_worker_scores_its_own_file_from_a_worktree(self):
        self.g("init")
        self.g("track", "add", "api")
        (self.top / "entry.txt").write_text("100\n")
        (self.top / "score.py").write_text("import sys\nprint(open(sys.argv[1]).read())\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "setup")
        self.g("wish", "go", "--track", "api", "--better", "lower")
        toml = self.top / "tracks" / "api" / "lab" / "genie.toml"
        toml.write_text(toml.read_text().replace('[tiers.T2]\ncmd = ""', "[tiers.T2]\ncmd = '\"%s\" score.py {entry}'"
                                                 % sys.executable))
        wt = self.tmp / "wt"
        self.git("worktree", "add", "-q", str(wt), "-b", "idea")
        (wt / "entry.txt").write_text("80\n")
        self.assertIn("score 80", self.g("score", "--tier", "T2", "--entry", "entry.txt", "--track", "api", cwd=wt))
        self.assertIn("score 100", self.g("score", "--tier", "T2", "--entry", "entry.txt", "--track", "api"))

    def test_status_runs(self):
        self.g("init")
        out = self.g("status")
        self.assertIn("SESSIONS", out)
        self.assertIn("keep-alive ON", out)
        cfg = json.loads((self.top / ".genie" / "config.json").read_text())
        self.assertEqual(cfg["version"], 1)

    def test_launcher_passes_exit_codes(self):
        if os.name == "nt":
            self.skipTest("the launcher is a POSIX shell script")
        launcher = GENIE.parent.parent / "bin" / "genie"
        link = self.tmp / "genie-link"
        link.symlink_to(launcher)
        for path in (launcher, link):
            p = subprocess.run(["sh", str(path), "vault", "nonsense"], cwd=self.top, capture_output=True, env=self.env)
            self.assertEqual(p.returncode, 1)  # not set up yet
            self.assertEqual(subprocess.run(["sh", str(path), "help"], cwd=self.top, capture_output=True,
                                            env=self.env).returncode, 0)
