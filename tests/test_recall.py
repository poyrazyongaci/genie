"""Receipts, the gap between a checkpoint and a compaction, and `genie recall`."""
import json
import subprocess
import sys

from helpers import GENIE, ProjectTest, Recording

ENTRY = """## Goal
Ship it.

## Next
Wire the cache.
===DURABLE===
>>> none: test
"""
SID = {"CLAUDE_CODE_SESSION_ID": "s1"}
CKPT = "genie checkpoint --track api wiring <<'ENTRY'"


class Base(ProjectTest):
    def setUp(self):
        super().setUp()
        self.g("init")
        self.g("track", "add", "api", "The API")
        self.rec = Recording(self.tmp / "rec.jsonl")

    def post(self, cmd, tid="", out="", sid="s1"):
        return self.hook("PostToolUse", session_id=sid, tool_name="Bash", tool_use_id=tid,
                         tool_input={"command": cmd}, tool_response={"stdout": out, "stderr": ""},
                         transcript_path=self.rec.write())

    def save(self, track="api", topic="wiring", entry=ENTRY, sid="s1", env=None):
        """The agent runs a real checkpoint and Claude Code reports the call to the hook."""
        tid, _ = self.rec.call("Bash", {"command": CKPT.replace("api", track)}, "saved")
        out = self.g("checkpoint", "--track", track, topic, stdin=entry,
                     env={"CLAUDE_CODE_SESSION_ID": sid} if env is None else env)
        self.post(CKPT.replace("api", track), tid, out, sid)
        return out

    def reload(self, source="compact", sid="s1"):
        code, out, err = self.hook("SessionStart", session_id=sid, source=source, transcript_path=self.rec.write())
        self.assertEqual((code, err), (0, ""))
        self.assertLess(len(out), 10_000)
        return out



class GapTest(Base):
    # ---- what counts as a checkpoint

    def test_only_a_saved_checkpoint_counts(self):
        for cmd in ("echo 'genie checkpoint --track api x'", "grep -n 'genie checkpoint' README.md",
                    "genie checkpoint --help", "genie checkpoint --track api x || true",
                    "pgrep -f 'genie checkpoint'", "cat scripts/checkpoint.py"):
            self.post(cmd, out="genie checkpoint --track api x\nstate: tracks/api/STATE.md (1 kept)")
            self.assertNotIn("ckpt_time", self.state(), cmd)
        out = self.g("checkpoint", "--track", "api", "x", stdin="## Next\nno durable part\n", ok=False)
        self.assertNotIn("receipt", out)                           # refused, so nothing to trust
        self.post("genie checkpoint --track api x", out=out)
        self.assertNotIn("ckpt_time", self.state())
        self.assertIn("receipt: ", self.save())
        st = self.state()
        self.assertEqual((st["track"], st["ckpt"]["topic"]), ("api", "wiring"))
        self.assertEqual(list((self.home / ".claude" / "genie" / "receipts").rglob("*.json")), [])   # taken once

    def test_a_checkpoint_run_in_the_background_counts_when_it_lands(self):
        self.rec.tokens = 150_000
        tid, _ = self.rec.call("Bash", {"command": CKPT, "run_in_background": True}, "Command running in background")
        self.post(CKPT, tid, "Command running in background with ID: b1")
        self.assertNotIn("ckpt_time", self.state())                # launched, not saved yet
        self.g("checkpoint", "--track", "api", "wiring", stdin=ENTRY, env=SID)
        self.rec.tokens = 156_000
        self.rec.call("Bash", {"command": "ls"})
        self.post("ls")
        st = self.state()
        self.assertEqual(st["ckpt_tokens"], 150_000)               # the state was written at launch
        self.assertEqual(st["ckpt"]["call"], tid)

    def test_output_piped_away_still_marks_the_call(self):
        tid, _ = self.rec.call("Bash", {"command": CKPT + " | tail -1"}, "git commit ...")
        self.g("checkpoint", "--track", "api", "wiring", stdin=ENTRY, env=SID)
        self.post(CKPT + " | tail -1", tid, "git commit -m notes")   # the receipt line was cut off
        self.assertEqual(self.state()["ckpt"]["call"], tid)

    def test_without_a_session_id_the_receipt_line_binds_it(self):
        out = self.save(env={})
        self.assertEqual(self.state()["ckpt"]["topic"], "wiring")
        rid = out.split("receipt: ")[1].split()[0]
        self.post("echo receipt: " + rid, out="receipt: " + rid, sid="s2")   # a copy of the line is not a save
        self.post("genie checkpoint --track api x", out="receipt: 1700000000000-deadbeef", sid="s2")
        self.assertNotIn("ckpt_time", self.state("s2"))

    def test_another_session_on_the_track_is_never_reloaded(self):
        self.save()
        other = Recording(self.tmp / "other.jsonl")
        self.g("checkpoint", "--track", "api", "theirs", env={"CLAUDE_CODE_SESSION_ID": "s2"},
               stdin=ENTRY.replace("Wire the cache", "Their plan, not yours"))
        self.hook("PostToolUse", session_id="s2", tool_name="Bash", tool_input={"command": "ls"},
                  transcript_path=other.write())
        self.assertEqual(self.state("s2")["ckpt"]["topic"], "theirs")
        self.assertIn("Their plan", (self.top / "tracks" / "api" / "STATE.md").read_text().split("<!-- checkpoint -->")[1])
        self.rec.boundary()
        mine, digest = self.reload().split("SHARED NOTES")
        self.assertIn("Wire the cache", mine)
        self.assertNotIn("Their plan", mine)                       # the newest entry on file is not this session's
        self.assertIn("Their plan", digest)                        # the shared digest still shows the track
        fresh = self.reload(sid="s3")                              # a session that never saved
        self.assertIn("No track checkpoint", fresh)

    def test_notes_only_checkpoint_keeps_the_last_real_state(self):
        self.save()
        self.save(track="none", topic="cross notes")
        st = self.state()
        self.assertEqual((st["track"], st["ckpt"]["topic"]), ("api", "cross notes"))
        self.rec.boundary()
        out = self.reload()
        self.assertIn("Wire the cache", out)
        self.assertIn("cross notes", out)
        self.assertIn("notes only", out)

    # ---- the gap

    def test_work_between_checkpoint_and_kept_messages_is_pasted(self):
        self.rec.call("Bash", {"command": "pytest -q old"}, "1 failed")
        self.save()
        self.rec.say("never use port 8080")
        self.rec.call("Bash", {"command": "pytest tests/api"}, "3 failed\nlong trace")
        self.rec.call("Edit", {"file_path": str(self.top / "api" / "server.py"), "old_string": "a", "new_string": "b"})
        self.rec.call("Read", {"file_path": "docs/side.md"}, side=True)
        self.rec.note("The port clash explains all three failures.")
        _, head = self.rec.call("Bash", {"command": "pytest tests/api -x"}, "all passed")
        self.rec.boundary(head)
        out = self.reload()
        self.assertIn("Wire the cache", out)                       # the saved state
        for part in ("never use port 8080", "pytest tests/api -> 3 failed", "Edit: api", "server.py",
                     "The port clash explains", "newer than the state", "genie recall"):
            self.assertIn(part, out)
        for part in ("pytest tests/api -x", "pytest -q old", "docs/side.md", "long trace", CKPT, str(self.top)):
            self.assertNotIn(part, out)                            # kept, older, a subagent's, cut, the save itself
        self.assertLess(out.index("Wire the cache"), out.index("never use port 8080"))

    def test_no_gap_says_the_state_is_complete(self):
        self.save()
        _, head = self.rec.call("Bash", {"command": "pytest"}, "ok")
        self.rec.boundary(head)
        out = self.reload()
        self.assertIn("The state is complete", out)
        self.assertNotIn("genie recall", out)

    def test_no_checkpoint_pastes_the_stretch_since_the_last_compaction(self):
        self.rec.say("old instruction")
        self.rec.boundary()
        self.reload()
        self.rec.say("use the staging database")
        self.rec.call("Bash", {"command": "make deploy"}, "done")
        _, head = self.rec.call("Bash", {"command": "make check"}, "ok")
        self.rec.boundary(head)
        out = self.reload()
        self.assertIn("No track checkpoint", out)
        self.assertIn("use the staging database", out)
        self.assertIn("make deploy", out)
        self.assertNotIn("old instruction", out)                   # the earlier cycle handled it
        self.assertNotIn("make check", out)

    def test_a_recording_without_the_boundary_pastes_to_the_end(self):
        self.save()
        self.rec.say("ship on friday")
        out = self.reload()                                        # Claude Code has not written the boundary
        self.assertIn("ship on friday", out)
        self.assertIn("may repeat", out)

    def test_the_place_where_compaction_was_let_through_ends_the_gap(self):
        self.save()
        self.rec.say("ship on friday")
        self.rec.call("Bash", {"command": "make build"}, "built")
        code, _, _ = self.hook("PreCompact", trigger="auto", transcript_path=self.rec.write())
        self.assertEqual(code, 0)                                  # fresh checkpoint, so it passes
        self.rec.call("Bash", {"command": "make later"}, "kept by Claude Code")
        out = self.reload()                                        # still no boundary in the file
        self.assertIn("ship on friday", out)
        self.assertIn("make build", out)
        self.assertNotIn("make later", out)
        self.assertIn("last few of these lines may repeat", out)

    def test_a_checkpoint_after_the_pass_leaves_no_gap(self):
        self.save()
        self.hook("PreCompact", trigger="auto", transcript_path=self.rec.write())
        self.rec.call("Bash", {"command": "make more"}, "ok")
        self.save(topic="second")
        self.rec.call("Bash", {"command": "make later"}, "ok")
        out = self.reload()
        self.assertIn("The state is complete", out)
        self.assertNotIn("make", out.split("SHARED NOTES")[0])

    def test_a_long_gap_keeps_the_messages_and_the_newest_calls(self):
        self.save()
        self.rec.say("the customer is ACME, " + "x" * 3000)
        for i in range(300):
            self.rec.call("Bash", {"command": f"step {i} " + "y" * 200}, "ok " * 50)
        _, head = self.rec.call("Bash", {"command": "last"}, "ok")
        self.rec.boundary(head)
        out = self.reload()
        self.assertIn("the customer is ACME", out)
        self.assertIn("step 299", out)
        self.assertNotIn("step 0 ", out)
        self.assertIn("earlier lines left out", out)
        self.assertIn("Wire the cache", out)

    def test_time_places_the_checkpoint_when_the_call_is_unknown(self):
        self.rec.say("before the save", at="2000-01-01T00:00:00.000Z")
        self.g("checkpoint", "--track", "api", "wiring", stdin=ENTRY, env=SID)
        self.post("ls")                                            # claims the receipt, no call id known
        self.rec.say("after the save")
        self.rec.boundary()
        out = self.reload()
        self.assertIn("after the save", out)
        self.assertNotIn("before the save", out)

    def test_resume_gets_the_state_and_the_way_back(self):
        self.save()
        out = self.reload(source="resume")
        self.assertIn("Wire the cache", out)
        self.assertIn("genie recall", out)
        self.assertNotIn("just compacted", out)
        self.assertNotIn("genie recall", self.reload(source="startup", sid="s9"))

    def test_parallel_hooks_keep_the_session_state_whole(self):
        d = {"session_id": "s1", "hook_event_name": "PostToolUse", "cwd": str(self.top), "tool_name": "Bash",
             "tool_input": {"command": "ls"}, "transcript_path": self.rec.write()}
        env = {**self.env, "CLAUDE_PROJECT_DIR": str(self.top)}
        procs = [subprocess.Popen([sys.executable, str(GENIE), "hook"], cwd=self.top, env=env, text=True,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(12)]
        for p in procs:
            _, err = p.communicate(json.dumps(d), timeout=60)
            self.assertEqual((p.returncode, err), (0, ""))
        self.assertIn("seen", self.state())
        left = [p.name for p in (self.home / ".claude" / "genie" / "sessions").iterdir()]
        self.assertEqual(left, ["s1.json"])                        # no temp file, no lock left behind


class RecallTest(Base):
    def recall(self, *args, ok=True, env=SID):
        return self.g("recall", *args, ok=ok, env=env)

    def fill(self):
        self.rec.say("never use port 8080")
        self.rec.call("Bash", {"command": "pytest tests/api"}, "3 failed\n" + "trace line\n" * 900 + "THE END")
        self.save()
        self.rec.say("ship on friday")
        self.rec.call("Read", {"file_path": "docs/side.md"}, "subagent port notes", side=True)
        self.rec.call("Bash", {"command": "curl localhost:9090"}, "port 9090 answers")
        self.post("ls")

    def test_search_finds_text_across_the_whole_recording(self):
        self.fill()
        out = self.recall("PORT")
        self.assertIn("never use port 8080", out)
        self.assertIn("port 9090 answers", out)
        self.assertNotIn("subagent port notes", out)
        self.assertLess(out.index("9090"), out.index("8080"))      # newest first
        self.assertIn("2 found", out)
        self.assertIn("nothing found", self.recall("port", "nonsense-word"))
        self.assertIn("1 found", self.recall("port", "answers"))    # every word must be there

    def test_at_prints_one_entry_in_full_and_pages(self):
        self.fill()
        hit = [ln for ln in self.recall("3 failed").splitlines() if ln.startswith("@")][0]
        at = hit.split()[0][1:]
        out = self.recall("--at", at)
        self.assertIn("3 failed", out)
        self.assertNotIn("THE END", out)
        self.assertIn("--skip", out)
        skip = out.split("--skip ")[1].split()[0].rstrip("`.")
        self.assertIn("THE END", self.recall("--at", at, "--skip", skip, "--chars", "20000"))

    def test_gap_lists_everything_after_the_last_checkpoint(self):
        self.fill()
        out = self.recall("--gap")
        self.assertIn("ship on friday", out)
        self.assertIn("curl localhost:9090", out)
        self.assertNotIn("never use port 8080", out)

    def test_it_says_so_when_the_recording_is_gone(self):
        self.fill()
        self.rec.path.unlink()
        out = self.recall("port", ok=False)
        self.assertIn("cleanupPeriodDays", out)
        self.assertIn("no session", self.recall("port", ok=False, env={"CLAUDE_CODE_SESSION_ID": "nobody"}))

    def test_another_recording_can_be_named(self):
        self.fill()
        out = self.recall("friday", "--file", str(self.rec.path), env={})
        self.assertIn("ship on friday", out)
