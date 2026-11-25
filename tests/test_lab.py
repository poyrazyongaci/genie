#!/usr/bin/env python3
"""End to end test of the genie lab on a toy problem in a temp folder.

    python3 -m unittest discover -s tests

The toy: an entry is a text file holding one number. The scorer prints it.
Lower is better. Nothing here touches a real track.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
GENIE = SCRIPTS / "genie.py"
sys.path.insert(0, str(SCRIPTS))
import genie_lab  # noqa: E402

TOML = '''track = "toy"
better = "lower"
width = 3
keep_runs = 2
[tiers.T0]
cmd = '"{py}" scorer/score.py {{entry}}'
[tiers.T1]
cmd = '"{py}" scorer/score.py {{entry}}'
[tiers.T2]
cmd = '"{py}" scorer/score.py {{entry}}'
[scorer]
paths = ["scorer"]
[solution]
paths = ["entries"]
[preflight]
tools = ["git"]
'''


class LabTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="genie-test-"))
        (cls.root / "tracks" / "toy").mkdir(parents=True)
        (cls.root / "scorer").mkdir()
        (cls.root / "scorer" / "score.py").write_text(
            "import sys\nprint('score', open(sys.argv[1]).read().strip())\n", encoding="utf-8")
        (cls.root / "entries").mkdir()
        for name, val in (("worse", 120), ("base", 100), ("better", 80), ("tiny", 99.5), ("bad", 130)):
            (cls.root / "entries" / name).write_text(str(val), encoding="utf-8")
        cls.env = {**os.environ, "GENIE_ROOT": str(cls.root), "GENIE_TRACK": "toy"}
        cls.lab = cls.root / "tracks" / "toy" / "lab"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def g(self, *args, ok=True, env=None):
        p = subprocess.run([sys.executable, str(GENIE), *args], capture_output=True, text=True,
                           env={**self.env, **(env or {})})
        out = p.stdout + p.stderr
        if ok is not None:
            self.assertEqual(p.returncode == 0, ok, f"{args} -> {p.returncode}\n{out}")
        return out

    def test_full_run(self):
        e = lambda n: f"entries/{n}"  # noqa: E731

        # stage 0 to 1
        out = self.g("wish", "make the number small", "--better", "lower")
        self.assertIn("stage 0 Preflight", out)
        self.g("wish", "again", "--better", "lower", ok=False)  # never overwrite a lab
        self.g("preflight")
        self.assertIn("stage 1 Plan", self.g("next"))
        wish = self.lab / "WISH.md"
        wish.write_text(wish.read_text(encoding="utf-8").replace("TODO", "filled"), encoding="utf-8")

        # stage 2: each check fails in turn
        self.assertIn("no T2 score command", self.g("next"))
        (self.lab / "genie.toml").write_text(TOML.format(py=sys.executable), encoding="utf-8")
        self.assertIn("no golden entry", self.g("next"))
        self.g("freeze", ok=False)  # freeze before trust is refused
        self.g("golden", "add", e("base"), "--score", "55")       # a wrong known score
        self.g("golden", ok=False)
        self.assertIn("replaced", self.g("golden", "add", e("base"), "--score", "100"))  # same entry and tier
        self.g("golden", "add", e("worse"), "--score", "1", "--tier", "T1")
        self.assertIn("Removed 1", self.g("golden", "remove", e("worse")))
        self.assertEqual(len(genie_lab.read_golden(self.lab)), 1)
        self.g("golden")
        self.assertIn("order test", self.g("next"))
        self.g("order", e("base"), e("worse"), ok=False)  # wrong way round
        self.g("order", e("worse"), e("base"), e("better"))
        self.assertIn("noise not measured", self.g("next"))
        self.g("score", "--tier", "T2", "--entry", e("base"), "--runs", "3", "--noise")
        self.assertIn("scorer not frozen", self.g("next"))
        self.g("freeze")

        # stage 3
        self.assertIn("no base row at T2", self.g("next"))
        self.g("score", "--tier", "T2", "--entry", e("base"), "--base", "--runs", "2")
        self.assertIn("no board score", self.g("next"))
        self.g("log", "--tier", "T3", "--score", "104", "--verdict", "base", "--evidence", "abc1234")

        # stage 4
        self.assertIn("stage 4 Scout", self.g("next"))
        self.g("idea", "add", "use the better file", "--test", "score it", "--files", "entries/better")
        self.g("idea", "add", "tiny tweak", "--test", "score it", "--files", "entries/tiny", "--rank", "5")
        self.g("idea", "add", "bad idea", "--test", "score it", "--files", "entries/bad", "--rank", "6")

        # stage 5: fan out is offered because the three ideas touch disjoint files
        out = self.g("next")
        self.assertIn("stage 5 Iterate", out)
        self.assertIn("fan out I1 I2 I3", out)

        # one winning iteration
        self.g("idea", "start", "I1")
        out = self.g("score", "--tier", "T2", "--entry", e("better"), "--idea", "I1", "--runs", "2")
        self.assertIn("win", out)
        rows = genie_lab.read_ledger(self.lab)
        win = rows[-1]["id"]
        self.g("keep", win, ok=False)  # a keep needs evidence
        self.g("keep", win, "--evidence", "def5678")
        self.assertIn("Base: def5678", self.g("next"))

        # a loss cannot be kept
        out = self.g("score", "--tier", "T2", "--entry", e("bad"), "--idea", "I3", "--runs", "2")
        self.assertIn("loss", out)
        loss = genie_lab.read_ledger(self.lab)[-1]["id"]
        self.g("keep", loss, "--evidence", "x", ok=False)
        self.g("reject", loss)

        # three failed attempts in a row send the conductor to scout
        for n in ("a", "b"):
            self.g("score", "--tier", "T2", "--entry", e("bad"), "--idea", f"x{n}")
        self.assertIn("scout again", self.g("next"))
        self.g("scouted")
        self.assertNotIn("scout again", self.g("next"))

        # the ledger is append only and ids are unique
        rows = genie_lab.read_ledger(self.lab)
        self.assertEqual([r["id"] for r in rows], [str(i) for i in range(1, len(rows) + 1)])

        # a solver that edits the scorer gets caught at the next score
        sc = self.root / "scorer" / "score.py"
        keep = sc.read_text(encoding="utf-8")
        sc.write_text(keep.replace("strip())", "strip(), end='')\nprint()"), encoding="utf-8")
        self.assertIn("scorer changed", self.g("score", "--tier", "T2", "--entry", e("tiny"), ok=False))
        self.assertIn("stage 2 Scorer", self.g("next"))
        self.g("freeze", ok=False)
        sc.write_text(keep, encoding="utf-8")
        self.assertIn("stage 5", self.g("next"))

        # gates
        self.g("gate", "add", "push def5678 to the public repo", "--kind", "push")
        self.assertIn("toy-G1", self.g("gate", "list"))
        self.assertIn("Waiting on the human: toy-G1 push", self.g("next"))
        push = {"tool_name": "Bash", "tool_input": {"command": "cd x && git push origin main"}}
        os.environ["GENIE_ROOT"] = str(self.root)
        self.assertIn("needs the human", genie_lab.rule_check(push))
        self.g("gate", "done", "toy-G1", "--answer", "yes")
        self.assertEqual(genie_lab.rule_check(push), "")       # the approval lets one push through
        self.assertIn("needs the human", genie_lab.rule_check(push))  # and only one
        for cmd in ("git status", "genie bridge ask x", "git log --grep push"):
            self.assertEqual(genie_lab.rule_check({"tool_name": "Bash", "tool_input": {"command": cmd}}), "", cmd)

        # a standing approval lasts until it is revoked, and a project gate needs no track
        self.g("gate", "add", "push docs any time", "--kind", "push", "--standing", "--track", "")
        self.assertIn("needs the human", genie_lab.rule_check(push))
        self.g("gate", "done", "g1")
        self.assertEqual([genie_lab.rule_check(push) for _ in range(3)], ["", "", ""])
        self.g("gate", "done", "G1", "--answer", "no more")
        self.assertIn("needs the human", genie_lab.rule_check(push))

        # agents never answer a gate and never write the records by hand
        for cmd in ("genie gate done G1", 'sh "/x/bin/genie" gate done G1', "cd a && genie  gate  done G2 --answer yes"):
            self.assertIn("only the human", genie_lab.rule_check(
                {"tool_name": "Bash", "tool_input": {"command": cmd}}), cmd)
        for f in (self.lab / "GATES.md", self.lab / "ledger.tsv", self.lab / "state.json",
                  self.root / ".genie" / "GATES.md"):
            self.assertIn("genie command only", genie_lab.rule_check(
                {"tool_name": "Write", "tool_input": {"file_path": str(f)}}), f)
        self.assertEqual(genie_lab.rule_check(
            {"tool_name": "Write", "tool_input": {"file_path": str(self.root / "entries" / "state.json")}}), "")

        # split duties
        wt = self.root
        (wt / ".genie-role").write_text(json.dumps({"role": "solver", "track": "toy"}), encoding="utf-8")
        edit = lambda p: {"tool_name": "Edit", "tool_input": {"file_path": str(wt / p)}}  # noqa: E731
        self.assertIn("belongs to the scorer", genie_lab.rule_check(edit("scorer/score.py")))
        self.assertEqual(genie_lab.rule_check(edit("entries/base")), "")
        (wt / ".genie-role").write_text(json.dumps({"role": "scorer", "track": "toy"}), encoding="utf-8")
        self.assertIn("solution code", genie_lab.rule_check(edit("entries/base")))
        (wt / ".genie-role").unlink()
        self.assertEqual(genie_lab.rule_check(edit("scorer/score.py")), "")
        del os.environ["GENIE_ROOT"]

        # locks
        self.g("lock", "timing", env={"GENIE_WORKER": "w1"})
        self.assertIn("held by w1", self.g("lock", "timing", env={"GENIE_WORKER": "w2"}, ok=False))
        self.g("unlock", "timing", env={"GENIE_WORKER": "w2"}, ok=False)
        self.g("unlock", "timing", env={"GENIE_WORKER": "w1"})
        self.g("lock", "timing", env={"GENIE_WORKER": "w2"})

        # brief, time, ledger and status do not crash
        self.assertIn("not a commit in this repo", self.g("brief", "I2"))  # base def5678 is made up
        self.assertIn("score", self.g("time"))
        self.g("ledger")

    def test_adopt(self):
        t = self.root / "tracks" / "old"
        t.mkdir(parents=True)
        (t / "SCORES.md").write_text(
            "# old submissions\n\n| Time (ET) | Commit | Local score | Board score | Note |\n|---|---|---|---|---|\n"
            "| Fri 18:42 | a6fdec1 | 1104.8 | 1472.8 | baseline |\n"
            "| Fri 19:21 | a7bd86c | 176.6 | not submitted | replaced |\n"
            "| Sat 04:43 | d639f74 | 97.4 sim | 175.5 (168.8 sim + 6.7 CPU) | lanes |\n", encoding="utf-8")
        (t / "STATE.md").write_text(
            "# state\n\n<!-- checkpoint -->\n## Checkpoint now\n\n## Goal\nReach 140 s on the board.\n\n"
            "## Next\nWait for the board score. Then try mid-size rules.\n\n## Blocked\nNone.\n", encoding="utf-8")
        out = self.g("adopt", "--track", "old", "--better", "lower")
        self.assertIn("Wrote 2 board rows", out)
        rows = genie_lab.read_ledger(t / "lab")
        self.assertEqual([(r["score"], r["verdict"], r["evidence"]) for r in rows],
                         [("1472.8", "info", "a6fdec1"), ("175.5", "base", "d639f74")])
        self.assertTrue(all(r["tier"] == "T3" for r in rows))  # local numbers are not comparable, never T2 rows
        nxt = self.g("next", "--track", "old")
        self.assertIn("best board T3: 175.5", nxt)
        self.assertIn("Base: d639f74", nxt)
        self.assertIn("Reach 140 s", (t / "lab" / "WISH.md").read_text(encoding="utf-8"))
        self.assertIn("1 backlog ideas", out)  # a paragraph is one idea
        self.assertIn("GENIE lab old: stage 1", self.g("next", env={"GENIE_TRACK": ""}))

    def test_pinned_golden(self):
        """A track whose only true scorer is the board pins its own scores."""
        root = Path(tempfile.mkdtemp(prefix="genie-pin-"))
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copytree(self.root / "scorer", root / "scorer")
        shutil.copytree(self.root / "entries", root / "entries")
        (root / "tracks" / "pin").mkdir(parents=True)
        env = {"GENIE_ROOT": str(root), "GENIE_TRACK": "pin"}
        self.g("wish", "board only", "--better", "lower", env=env)
        self.g("preflight", env=env)
        lab = root / "tracks" / "pin" / "lab"
        w = lab / "WISH.md"
        w.write_text(w.read_text(encoding="utf-8").replace("TODO", "filled"), encoding="utf-8")
        (lab / "genie.toml").write_text(TOML.format(py=sys.executable).replace('"toy"', '"pin"'), encoding="utf-8")
        self.assertIn("--source pinned", self.g("golden", env=env, ok=False))
        self.g("golden", "add", "entries/base", "--score", "100", "--source", "pinned", env=env)
        self.g("golden", env=env)
        self.g("order", "entries/worse", "entries/base", env=env)
        self.assertIn("pinned only", self.g("next", env=env))  # two entries are not enough
        self.g("order", "entries/worse", "entries/base", "entries/better", env=env)
        out = self.g("next", env=env)
        self.assertIn("noise not measured", out)
        self.assertIn("Scorer trust: pinned only", out)

        # the lab may be renamed to genie-lab, and the scorer may live outside the checkout
        lab.rename(lab.parent / "genie-lab")
        self.assertIn("noise not measured", self.g("next", env=env))
        self.assertIn("GENIE lab pin", self.g("next", env={**env, "GENIE_TRACK": ""}))
        outside = Path(tempfile.mkdtemp(prefix="genie-out-"))
        self.addCleanup(shutil.rmtree, outside, True)
        shutil.copy(root / "scorer" / "score.py", outside / "score.py")
        toml = lab.parent / "genie-lab" / "genie.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace(
            'paths = ["scorer"]', f"paths = ['{outside.as_posix()}/*.py']"), encoding="utf-8")
        os.environ["GENIE_ROOT"] = str(root)
        cfg = genie_lab.load_cfg(lab.parent / "genie-lab")
        h1, n = genie_lab.scorer_hash(cfg)
        self.assertEqual(n, 1)
        (outside / "score.py").write_text("print(1)\n", encoding="utf-8")
        self.assertNotEqual(h1, genie_lab.scorer_hash(cfg)[0])
        del os.environ["GENIE_ROOT"]

    def test_pure(self):
        self.assertAlmostEqual(genie_lab.spearman([(1, 1), (2, 2), (3, 3), (4, 4)]), 1.0)
        self.assertAlmostEqual(genie_lab.spearman([(1, 4), (2, 3), (3, 2), (4, 1)]), -1.0)
        a = {"files": "bot/a.py, bot/b.py"}
        self.assertFalse(genie_lab.isolated_pair(a, {"files": "bot/b.py"}))
        self.assertFalse(genie_lab.isolated_pair(a, {"files": "bot"}))
        self.assertTrue(genie_lab.isolated_pair(a, {"files": "sim/x.py"}))
        self.assertFalse(genie_lab.isolated_pair(a, {"files": ""}))  # unknown files are never isolated
        self.assertEqual(genie_lab.parse_score({}, "noise 3\nfinal 97.5 s\n\n"), 97.5)
        self.assertEqual(genie_lab.parse_score({"parse": {"regex": r"total=(\d+)"}}, "total=7\nx 9\n"), 7.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
