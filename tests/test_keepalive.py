"""The hooks that carry a session across compactions."""
import json
import time

from helpers import ProjectTest

ENTRY = """## Goal
Ship it.

## Next
Wire the cache.
===DURABLE===
>>> none: test
"""


class KeepAliveTest(ProjectTest):
    def bash(self, cmd, tokens):
        return self.hook("PostToolUse", tool_name="Bash", tool_input={"command": cmd},
                         transcript_path=self.transcript(tokens))

    def compact(self, tokens, trigger="auto"):
        return self.hook("PreCompact", trigger=trigger, transcript_path=self.transcript(tokens))

    def test_silent_without_opt_in(self):
        for event in ("SessionStart", "PostToolUse", "PreCompact", "PreToolUse"):
            code, out, err = self.hook(event, tool_name="Bash", tool_input={"command": "git push"},
                                       transcript_path=self.transcript(239_000), trigger="auto", source="startup")
            self.assertEqual((code, out, err), (0, "", ""), event)
        self.assertFalse((self.home / ".claude" / "genie").exists())

    def test_nudge_hold_release_reload(self):
        self.g("init")
        self.g("track", "add", "api", "The API")
        self.assertEqual(self.bash("ls", 100_000)[1], "")          # far from the limit
        out = self.bash("ls", 185_000)[1]
        self.assertIn("genie:checkpoint", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.bash("ls", 190_000)[1], "")          # no repeat inside 15K
        code, _, err = self.compact(200_000)
        self.assertEqual(code, 2)                                  # held: no checkpoint yet
        self.assertIn("no checkpoint yet", err)
        self.assertIn("on hold", self.bash("ls", 201_000)[1])      # and the agent is told
        self.g("checkpoint", "--track", "api", "wiring", stdin=ENTRY)
        self.assertEqual(self.bash("genie checkpoint --track api wiring", 202_000)[1], "")
        self.assertEqual(self.state()["track"], "api")
        code, out, _ = self.compact(205_000)
        self.assertEqual(code, 0)                                  # fresh checkpoint, let it through
        self.assertIn("tracks/api/STATE.md", out)
        code, out, _ = self.hook("SessionStart", source="compact")
        self.assertIn("just compacted", out)
        self.assertIn("Wire the cache", out)
        self.assertLess(len(out), 10_000)

    def test_stale_checkpoint_holds_again(self):
        self.g("init")
        self.g("track", "add", "api")
        self.bash("genie checkpoint --track api x", 185_000)
        self.assertEqual(self.compact(190_000)[0], 0)              # 5K past it
        code, _, err = self.compact(199_000)                       # 14K past it
        self.assertEqual(code, 2)
        self.assertIn("last checkpoint was at 185K", err)

    def test_a_hold_below_the_nudge_point_is_told_at_once(self):
        self.g("init")
        self.assertEqual(self.compact(150_000)[0], 2)
        self.assertIn("on hold", self.bash("ls", 151_000)[1])

    def test_nudge_leaves_room_before_compaction_at_any_window(self):
        for window, quiet, nudged in ((250_000, 175_000, 182_000), (500_000, 390_000, 400_000),
                                      (1_000_000, 825_000, 835_000)):
            self.g("init")
            self.g("on", "--window", str(window))
            (self.home / ".claude" / "genie" / "sessions" / "s1.json").unlink(missing_ok=True)
            self.assertEqual(self.bash("ls", 60_000)[1], "")       # sets the floor
            self.assertEqual(self.bash("ls", quiet)[1], "", window)
            self.assertIn("genie:checkpoint", self.bash("ls", nudged)[1], window)

    def test_nudge_counts_from_the_floor(self):
        self.g("init")
        self.g("on", "--window", "150000")
        self.assertEqual(self.bash("ls", 103_000)[1], "")          # no work done yet, so nothing to save
        self.assertEqual(self.state()["floor"], 103_000)
        self.assertEqual(self.bash("ls", 120_000)[1], "")
        self.assertIn("genie:checkpoint", self.bash("ls", 124_000)[1])

    def test_floor_is_taken_again_after_a_compaction(self):
        self.g("init")
        self.bash("ls", 40_000)
        self.bash("ls", 90_000)
        self.assertEqual(self.state()["floor"], 40_000)
        self.hook("SessionStart", source="compact")
        self.assertNotIn("floor", self.state())
        self.bash("ls", 103_000)
        self.assertEqual(self.state()["floor"], 103_000)

    def test_a_high_floor_never_hides_the_nudge(self):
        self.g("init")                                             # hooks joined a session already at 210K
        self.bash("ls", 210_000)
        self.assertIn("genie:checkpoint", self.bash("ls", 218_000)[1])
        self.g("on", "--window", "100000")
        self.assertIn("genie:checkpoint", self.bash("ls", 240_000)[1])

    def test_never_traps_a_session(self):
        self.g("init")
        self.assertEqual(self.compact(245_000)[0], 0)              # past the ceiling
        self.assertEqual(self.compact(200_000, trigger="manual")[0], 0)
        codes = [self.compact(200_000)[0] for _ in range(27)]
        self.assertEqual(codes[:25], [2] * 25)
        self.assertEqual(codes[25:], [0, 0])                       # the hold cap

    def test_dry_run_and_lookalikes_do_not_count(self):
        self.g("init")
        for cmd in ("genie checkpoint --track api x --dry-run", "echo checkpoint", "genie status"):
            self.bash(cmd, 185_000)
            self.assertNotIn("ckpt_time", self.state(), cmd)
        (self.top / ".genie" / "config.json").write_text(json.dumps({"checkpoint_pattern": r"make save"}))
        self.bash("make save", 186_000)
        self.assertIn("ckpt_time", self.state())

    def test_subagents_and_broken_input_are_ignored(self):
        self.g("init")
        code, out, _ = self.hook("PostToolUse", agent_id="a1", tool_name="Bash", tool_input={"command": "ls"},
                                 transcript_path=self.transcript(200_000))
        self.assertEqual((code, out), (0, ""))
        code, out, _ = self.hook("PostToolUse", tool_name="Bash", tool_input=None, transcript_path="/nope")
        self.assertEqual((code, out), (0, ""))

    def test_rules_bind_subagents_and_can_be_off(self):
        self.g("init")
        push = dict(tool_name="Bash", tool_input={"command": "git push"})
        self.assertEqual(self.hook("PreToolUse", agent_id="a1", **push)[0], 2)
        cfg = self.top / ".genie" / "config.json"
        d = json.loads(cfg.read_text())
        d["hooks"]["rules"] = False
        cfg.write_text(json.dumps(d))
        self.assertEqual(self.hook("PreToolUse", **push)[0], 0)

    def test_on_off_keeps_other_settings(self):
        settings = self.top / ".claude" / "settings.local.json"
        settings.parent.mkdir()
        settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
        self.g("init")
        self.assertEqual(json.loads(settings.read_text())["autoCompactWindow"], 250_000)
        self.g("off")
        self.assertEqual(json.loads(settings.read_text()), {"permissions": {"allow": ["Bash(ls)"]}})
        self.assertEqual(self.bash("ls", 190_000)[1], "")
        settings.write_text("{ not json")
        self.assertIn("does not parse", self.g("on"))
        self.assertEqual(settings.read_text(), "{ not json")

    def test_hook_is_quick(self):
        self.g("init")
        t = time.time()
        self.bash("ls", 100_000)
        self.assertLess(time.time() - t, 3.0)
