#!/usr/bin/env python3
"""What compaction window is worth it, in money.

    genie window                     measure this project's sessions, then advise
    genie window --all               measure every project on this machine
    genie window --floor 90000 --growth 2000 --model claude-opus-5-5
                                     no history needed, say the numbers yourself

A bigger window means more requests between compactions, and every request
reads the whole context from the cache again. A smaller one means more
restarts, and every restart writes the floor to the cache again. The best
window is where the two meet:

    trigger = floor + sqrt(2 * growth * (restart + read * floor) / read)
    window  = trigger / 0.87

It reads transcripts and prints. It never changes a setting.
"""
import datetime as dt
import json
import statistics
import sys
import time
from collections import Counter

from genie_paths import COMPACT_RATIO, HOME

PRICED_ON = "2026-11-24"
# Dollars per million tokens: prompt limit, cache read, 5m write, 1h write, output.
# https://platform.claude.com/docs/en/about-claude/pricing
PRICES = {
    "claude-fable-5-2": [(None, 0.20, 10, 16, 40)],
    "claude-fable-5-1": [(None, 0.25, 12.5, 20, 50)],
    "claude-opus-5-5": [(None, 0.20, 5, 8, 20)],
    "claude-opus-5": [(None, 0.50, 6.25, 10, 25)],
    "claude-opus-4-8": [(None, 0.50, 6.25, 10, 25)],
    "claude-sonnet-5-5": [(None, 0.10, 2.5, 4, 10)],
    "claude-sonnet-5": [(None, 0.20, 2.5, 4, 10)],
    "claude-haiku-5-5": [(100_000, 0.01, 0.125, 0.20, 0.50), (None, 0.05, 0.625, 1, 2.50)],
}
# Not in a transcript, so these are estimates.
CKPT_OUT = 2_500        # output of the checkpoint call
REWORK_TOKENS = 4_000   # files read again after a compaction
REWORK_CALLS = 3        # and the calls that read them
SUMMARY = 7_000         # summary output when none was seen
CHARS_PER_TOKEN = 3
MIN_WINDOW, MAX_WINDOW, STEP = 100_000, 1_000_000, 5_000
MIN_REQUESTS = 20
SHOWN = (150, 200, 250, 300, 400, 500, 700, 1000)


# ---------------------------------------------------------------- measuring

def to_seconds(stamp):
    try:
        return dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def read_session(path):
    """(requests, indexes that follow a compaction, summary sizes), main chain only."""
    calls, at, starts, summaries = [], {}, [], []
    with open(path, "rb") as fh:
        for raw in fh:
            if b'"usage"' not in raw and b'"compact_boundary"' not in raw and b'"isCompactSummary"' not in raw:
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("isSidechain"):
                continue
            m = d.get("message") if isinstance(d.get("message"), dict) else {}
            if d.get("subtype") == "compact_boundary":
                starts.append(len(calls))
            elif d.get("isCompactSummary"):
                c = m.get("content")
                summaries.append(len(c if isinstance(c, str) else json.dumps(c)))
            elif d.get("type") == "assistant":
                u, model = m.get("usage") or {}, str(m.get("model", ""))
                if not u or not model or model.startswith("<"):
                    continue
                read, write = u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0
                by_ttl = u.get("cache_creation") or {}
                r = {"model": model.split("[")[0], "t": to_seconds(d.get("timestamp")), "read": read,
                     "ctx": (u.get("input_tokens") or 0) + read + write, "out": u.get("output_tokens") or 0,
                     "w1h": by_ttl.get("ephemeral_1h_input_tokens") or 0,
                     "w5m": by_ttl.get("ephemeral_5m_input_tokens") or 0}
                if not r["ctx"]:
                    continue
                # Claude Code writes one line per content block. The last one has the full count.
                key = m.get("id") or len(calls)
                if key in at:
                    calls[at[key]] = r
                else:
                    at[key] = len(calls)
                    calls.append(r)
    return calls, [s for s in starts if s < len(calls)], summaries


def measure(paths):
    floors, firsts, shared, outs, sums, models = [], [], [], [], [], Counter()
    grown = steps = gaps5 = gaps60 = gaps = w1h = w5m = 0
    sessions = 0
    for path in paths:
        try:
            calls, starts, summaries = read_session(path)
        except OSError:
            continue
        if not calls:
            continue
        sessions += 1
        sums += summaries
        firsts.append(calls[0]["ctx"])
        # The second compaction of a pair with no call between them adds nothing.
        starts = sorted(set(starts) - {0})
        for s in starts:
            floors.append(calls[s]["ctx"])
            shared.append(calls[s]["read"])
        for a, b in zip([0] + starts, starts + [len(calls)]):
            grown += calls[b - 1]["ctx"] - calls[a]["ctx"]
            steps += b - 1 - a
        for c, prev in zip(calls[1:], calls):
            gap = c["t"] - prev["t"]
            gaps, gaps5, gaps60 = gaps + 1, gaps5 + (gap > 300), gaps60 + (gap > 3600)
        for c in calls:
            models[c["model"]] += 1
            outs.append(c["out"])
            w1h, w5m = w1h + c["w1h"], w5m + c["w5m"]
    if not outs:
        return None
    ttl = w1h / (w1h + w5m) if w1h + w5m else 1.0
    summary = round(statistics.median(sums) / CHARS_PER_TOKEN) if sums else SUMMARY
    first = round(statistics.median(firsts))
    return {"sessions": sessions, "requests": len(outs), "compactions": len(floors), "first": first,
            "floor": round(statistics.median(floors)) if floors else first + summary,
            "shared": round(statistics.median(shared)) if shared else 0,
            "growth": round(grown / steps) if steps and grown > 0 else 0,
            "summary": summary, "out": round(sum(outs) / len(outs)), "ttl_1h": round(ttl, 3),
            "miss": (gaps60 if ttl > 0.5 else gaps5) / gaps if gaps else 0.0, "models": dict(models)}


# ---------------------------------------------------------------- the math

def price(model, prompt):
    for limit, *rates in PRICES[model]:
        if limit is None or prompt <= limit:
            return rates
    return PRICES[model][-1][1:]


def known(model):
    """The price row for a model id. A dated id counts as its undated name."""
    name = model.split("[")[0]
    if name in PRICES:
        return name
    dated = [p for p in PRICES if name.startswith(p + "-") and name[len(p) + 1:].isdigit() and len(name) - len(p) == 9]
    return dated[0] if dated else None


def write_rate(rates, m):
    return m["ttl_1h"] * rates[2] + (1 - m["ttl_1h"]) * rates[1]


def restart(model, m, trigger):
    """Dollars for one compaction: summary, checkpoint, the floor written again, rework."""
    big, small = price(model, trigger), price(model, m["floor"])
    shared = min(m["shared"], m["floor"])
    return ((trigger * big[0] + (m["summary"] + CKPT_OUT) * big[3])
            + (m["floor"] - shared) * write_rate(small, m) + shared * small[0]
            + REWORK_CALLS * m["out"] * small[3] + REWORK_TOKENS * write_rate(small, m)) / 1e6


def cost(model, m, window):
    """Dollars per 1,000 requests at this window, everything counted. None when the floor does not fit."""
    trigger, floor = window * COMPACT_RATIO, m["floor"]
    span = trigger - floor
    if span < m["growth"]:
        return None
    n, total = 100, 0.0
    for i in range(n):  # requests spread evenly between the floor and the trigger
        ctx = floor + span * (i + 0.5) / n
        rates = price(model, ctx)
        w = write_rate(rates, m)
        total += ctx * rates[0] + m["miss"] * ctx * max(0.0, w - rates[0]) + m["growth"] * w + m["out"] * rates[3]
    return 1000 * (total / n / 1e6 + restart(model, m, trigger) * m["growth"] / span)


def table_of(model, m):
    rows = ((w, cost(model, m, w)) for w in range(MIN_WINDOW, MAX_WINDOW + 1, STEP))
    return {w: c for w, c in rows if c is not None}


def best(model, m):
    t = table_of(model, m)
    return min(t, key=t.get) if t else None


def good_range(model, m, slack=1.05):
    """The windows within 5 percent of the best. The curve is flat, so this is wide."""
    t = table_of(model, m)
    if not t:
        return None
    ok = [w for w, c in t.items() if c <= slack * min(t.values())]
    return min(ok), max(ok)


# ---------------------------------------------------------------- the report

def k(n):
    return f"{n / 1000:.0f}K"


def row(name, value):
    print(f"  {name:<28} {value}")


def fail(text):
    print(f"genie window: {text}", file=sys.stderr)
    return 1


def set_price(model, text):
    try:
        read, w5, w1, out = (float(x) for x in text.split(","))
    except ValueError:
        return False
    if min(read, w5, w1, out) <= 0:
        return False
    PRICES[model] = [(None, read, w5, w1, out)]
    return True


def newest(files, days):
    since, out = time.time() - days * 86400, []
    for p in files:
        try:
            if p.stat().st_mtime >= since:
                out.append(p)
        except OSError:
            pass
    return out


def every_transcript():
    return list((HOME / ".claude" / "projects").glob("*/*.jsonl"))


def report(args, files, current):
    """`files` are transcripts, `current` is the window in force or None."""
    given = {"--floor": args.floor, "--growth": args.growth}
    for flag, v in given.items():
        if v is not None and v <= 0:
            return fail(f"{flag} must be above 0")
    m = measure(newest(files, args.days))
    by_hand = args.floor is not None and args.growth is not None
    if not m and not by_hand:
        return fail(f"no transcripts of this project in the last {args.days:g} days. Try --all, "
                    "or give --floor and --growth")
    if m and m["requests"] < MIN_REQUESTS and not by_hand:
        return fail(f"only {m['requests']} requests measured, too few. Try --all or --days 60")
    seen = m
    m = dict(m or {"shared": 0, "summary": SUMMARY, "out": 1_000, "ttl_1h": 1.0, "miss": 0.0, "models": {}})
    if args.floor is not None:
        m["floor"] = args.floor
    if args.growth is not None:
        m["growth"] = args.growth
    if not m["growth"]:
        return fail("the context did not grow in these sessions, so there is nothing to size. Give --growth")

    models = [args.model.split("[")[0]] if args.model else [
        x for x, n in sorted(m["models"].items(), key=lambda t: -t[1]) if n >= 0.05 * m["requests"]]
    if args.price:
        if len(models) != 1 or not set_price(models[0], args.price):
            return fail("--price needs one --model and four numbers above 0: read,write5m,write1h,output per million")
    priced = [(x, known(x)) for x in models if known(x)]
    if not priced:
        return fail(f"no price for {', '.join(models) or 'any model'}. Give --model, and --price read,write5m,write1h,output")

    print("MEASURED" if seen else "GIVEN")
    if seen:
        row("requests", f"{seen['requests']} in {seen['sessions']} sessions, last {args.days:g} days")
        row("compactions", seen["compactions"])
        row("first request", k(seen["first"]))
    row("floor after compaction", k(m["floor"]) + (
        " (given)" if args.floor is not None else "" if seen["compactions"] else
        " (no compaction seen: first request plus a summary, so likely too low)"))
    row("growth per request", f"{m['growth']:,} tokens" + (" (given)" if args.growth is not None else ""))
    if seen:
        row("output per request", f"{m['out']:,} tokens")
        row("1-hour cache writes", f"{m['ttl_1h']:.0%}")
        row("gaps that lose the cache", f"{m['miss']:.2%} of requests")
    if current:
        usable = current * COMPACT_RATIO - m["floor"]
        row("window now", f"{k(current)}, compaction near {k(current * COMPACT_RATIO)}, "
            + (f"{k(usable)} usable, about {usable / m['growth']:.0f} requests" if usable > m["growth"]
               else "the floor does not fit. Raise it"))
    else:
        row("window now", "not set by genie")

    advice = []
    for model, name in priced:
        t = table_of(name, m)
        if not t:
            print(f"\n{model}: the floor does not fit any window up to {k(MAX_WINDOW)}")
            continue
        top = min(t, key=t.get)
        low, high = good_range(name, m)
        print(f"\n{model}: best {k(top)}, within 5 percent from {k(low)} to {k(high)}")
        print(f"  {'window':>7} {'compacts at':>12} {'usable':>8} {'requests':>9} {'$ per 1,000':>12}")
        for w in sorted({x * 1000 for x in SHOWN} | {top} | ({current} if current else set())):
            c = cost(name, m, w)
            at = w * COMPACT_RATIO
            mark = (" best" if w == top else "") + (" now" if w == current else "")
            if c is None:
                print(f"  {k(w):>7} {k(at):>12}  the floor does not fit{mark}")
            else:
                print(f"  {k(w):>7} {k(at):>12} {k(at - m['floor']):>8} "
                      f"{(at - m['floor']) / m['growth']:>9.0f} {c:>12.2f}{mark}")
        advice.append((model, top, low, high))
    skipped = [x for x in models if not known(x)]
    if skipped:
        print(f"\nno price for {', '.join(skipped)}, skipped. Use --model with --price")
    if not advice:
        return 1
    model, top, low, high = advice[0]
    print()
    if current and low <= current <= high:
        print(f"Keep {k(current)}. It is within 5 percent of the best for {model}.")
    else:
        print(f"Run `genie on --window {top}` in an opted-in project. It applies to sessions started or resumed later.")
    name, cut = known(model), m["floor"] - 30_000
    if cut > m["shared"]:
        t, low_t = table_of(name, m), table_of(name, {**m, "floor": cut})
        saved = 1 - min(low_t.values()) / min(t.values())
        print(f"A floor of {k(cut)} would save {saved:.0%} more. MCP servers, skills, CLAUDE.md and digests make the floor.")
    print(f"API prices of {PRICED_ON}. A subscription may weigh tokens in another way. Summary output and\n"
          "rework after a compaction are estimates. Lost detail and very long contexts cost quality, and\n"
          "that is not in these numbers.")
    return 0
