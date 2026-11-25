"""`genie window`, the cost of a compaction window."""
import json
import re
import sys

from helpers import SCRIPTS, ProjectTest

sys.path.insert(0, str(SCRIPTS))
import genie_window as gw  # noqa: E402


def call(i, ctx, prev, model="claude-opus-5-5", ttl="1h", minute=None, **extra):
    m = i if minute is None else minute
    new = ctx - prev
    return {"type": "assistant", "timestamp": f"2026-11-{20 + m // 1440:02d}T{m // 60 % 24:02d}:{m % 60:02d}:00.000Z",
            "message": {"id": f"m{i}", "model": model, "usage": {
                "input_tokens": 0, "cache_read_input_tokens": prev, "cache_creation_input_tokens": new,
                "cache_creation": {f"ephemeral_{ttl}_input_tokens": new}, "output_tokens": 1000}}, **extra}


class WindowTest(ProjectTest):
    def history(self, model="claude-opus-5-5", cycles=3, compact=True):
        d = self.home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.top))
        d.mkdir(parents=True)
        rows, i, ctx = [], 0, 40_000
        rows.append(call(0, ctx, 20_000, model))
        for c in range(cycles):
            for _ in range(50):
                i += 1
                rows += [call(i, ctx + 2_000, ctx, model)] * 2     # Claude Code writes one line per block
                ctx += 2_000
            if compact and c < cycles - 1:
                rows.append({"type": "system", "subtype": "compact_boundary"})
                rows.append({"type": "user", "isCompactSummary": True, "message": {"content": "x" * 18_000}})
                i, ctx = i + 1, 100_000
                rows.append(call(i, ctx, 24_000, model))
        rows.append(call(i + 1, 900_000, 0, model, isSidechain=True))
        rows.append(call(i + 2, 5, 0, "<synthetic>"))
        text = "\n".join(json.dumps(r) for r in rows)
        (d / "a.jsonl").write_text(text + "\n{broken\n", encoding="utf-8")
        return d / "a.jsonl"

    def test_measures_floor_and_growth(self):
        m = gw.measure([self.history()])
        self.assertEqual(m["requests"], 153)
        self.assertEqual(m["compactions"], 2)
        self.assertEqual(m["floor"], 100_000)
        self.assertEqual(m["first"], 40_000)
        self.assertEqual(m["shared"], 24_000)
        self.assertEqual(m["growth"], 2_000)
        self.assertEqual(m["summary"], 6_000)
        self.assertEqual(m["out"], 1_000)
        self.assertEqual((m["ttl_1h"], m["miss"]), (1.0, 0.0))
        self.assertEqual(m["models"], {"claude-opus-5-5": 153})

    def test_best_window_is_the_minimum(self):
        m = {"floor": 103_000, "growth": 1750, "shared": 24_500, "summary": 7_000, "out": 950,
             "ttl_1h": 1.0, "miss": 0.0008}
        for model, want in (("claude-opus-5-5", 263), ("claude-fable-5-2", 319)):
            best = gw.best(model, m)
            self.assertAlmostEqual(best / 1000, want, delta=15)     # closed form, checked by hand
            near = gw.cost(model, m, best)
            for w in (best - 60_000, best + 60_000, 1_000_000):
                self.assertGreater(gw.cost(model, m, w), near)
        low, high = gw.good_range("claude-opus-5-5", m)
        self.assertTrue(low < 250_000 < high)

    def test_direction_of_each_input(self):
        m = {"floor": 100_000, "growth": 1750, "shared": 24_500, "summary": 7_000, "out": 950,
             "ttl_1h": 1.0, "miss": 0.0}
        base = gw.best("claude-opus-5-5", m)
        self.assertLess(gw.best("claude-opus-5-5", {**m, "floor": 60_000}), base)
        self.assertGreater(gw.best("claude-opus-5-5", {**m, "growth": 5_000}), base)
        self.assertLess(gw.best("claude-opus-5-5", {**m, "miss": 0.05}), base)

    def test_prompts_past_a_price_tier_cost_more(self):
        m = {"floor": 40_000, "growth": 1750, "shared": 20_000, "summary": 7_000, "out": 950,
             "ttl_1h": 1.0, "miss": 0.0}
        self.assertLessEqual(gw.best("claude-haiku-5-5", m) * gw.COMPACT_RATIO, 105_000)

    def test_command_reports_and_changes_nothing(self):
        self.history()
        self.g("init")
        settings = self.top / ".claude" / "settings.local.json"
        before = settings.read_text()
        out = self.g("window")
        for part in ("floor after compaction", "100K", "claude-opus-5-5", "Keep 250K", "API prices"):
            self.assertIn(part, out)
        self.assertEqual(settings.read_text(), before)
        self.g("on", "--window", "900000")
        self.assertIn("genie on --window 270000", self.g("window"))
        self.assertIn("claude-fable-5-2", self.g("window", "--model", "claude-fable-5-2"))
        self.assertIn("claude-opus-5-5", self.g("window", "--model", "claude-opus-5-5[1m]"))

    def test_says_when_it_cannot_know(self):
        self.assertIn("no transcripts", self.g("window", ok=False))
        self.assertIn("no price", self.g("window", "--floor", "90000", "--growth", "2000",
                                         "--model", "other-1", ok=False))
        self.assertIn("claude-opus-5-5", self.g("window", "--floor", "90000", "--growth", "2000",
                                                "--model", "claude-opus-5-5"))
        self.assertIn("other-1", self.g("window", "--floor", "90000", "--growth", "2000", "--model", "other-1",
                                        "--price", "0.2,5,8,20"))
        self.g("window", "--floor", "90000", "--growth", "2000", "--model", "x", "--price", "1,2", ok=False)
        self.g("window", "--floor", "-5", "--growth", "2000", "--model", "claude-opus-5-5", ok=False)

    def test_no_compaction_seen_is_said(self):
        self.history(cycles=1, compact=False)
        self.assertIn("no compaction", self.g("window"))
