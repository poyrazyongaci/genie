#!/usr/bin/env python3
"""Genie. One command for a fleet of Claude Code sessions.

Set up
  genie init [--no-window]               opt this project in. Nothing runs before this
  genie track add <slug> ["title"]       a folder, a README and a status board row
  genie doctor                           check the install
  genie on | off                         keep-alive for this project
  genie on --window <tokens>             and set where compaction starts
  genie window                           what window is worth it, from your own sessions

Every day
  genie status                           sessions, tracks and labs at a glance
  genie checkpoint --track <slug> "..."  save state and file what was learned (stdin)
  genie recall <words> | --gap | --at N  read this session's recording back
  genie vault index | check | links      the shared notes: digest, lint, what links to what
  genie board ... | bridge ...           mail between sessions, on one machine or over git
  genie watch [--once | --alive]         no-model watcher, exits when something needs a look
  genie dash [port]                      live page on localhost
  genie second-opinion ask|track|last    a read-only colleague from another model family

Scored work (see genie_lab.py, all take --track <slug>)
  wish "<problem>" --better lower|higher       scaffold a lab for a new problem
  adopt --better lower|higher                  build a lab for work already running
  next                                         stage, failing check and the one next job
  preflight                                    tools, secrets and machine facts
  score --tier T1 --entry <path>               run a tier, time it, write a ledger row
  log / keep / reject / supersede / ledger     ledger rows, append only
  time                                         phase breakdown and tier agreement
  golden / order / freeze                      scorer trust checks
  idea / brief / scouted / base                backlog and worker briefs
  gate add | list                              the waiting-on-you queue
  lock / unlock / locks                        exclusive resources

Keep-alive, how it works
  1. `autoCompactWindow` makes Claude Code compact near 87 percent of WINDOW.
  2. A PostToolUse hook reads the session's context size from its transcript.
     About 36K tokens before that point it tells the agent to run a checkpoint.
     After a compaction it waits for 20K tokens of new work first.
  3. A PreCompact hook holds automatic compaction until that checkpoint
     exists. Past CEILING tokens it lets compaction through anyway.
  4. A checkpoint that was really saved leaves a receipt. The hooks trust the
     receipt, never the text of a command, and note where in the session's
     recording the checkpoint sits.
  5. A SessionStart hook gives the session its own last state back after a
     compaction, plus the work between that checkpoint and the messages
     Claude Code kept, read from the recording.
"""
import argparse
import calendar
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

if sys.version_info < (3, 11):
    sys.stderr.write("genie needs Python 3.11 or newer, this is %d.%d\n" % sys.version_info[:2])
    sys.exit(0 if sys.argv[1:2] == ["hook"] else 1)  # a hook must never stop a session

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genie_paths  # noqa: E402
from genie_paths import (COMPACT_RATIO, HOME, RECEIPTS, STATE_HOME, Lock, read_json,  # noqa: E402
                         session_key, write_json)

# Claude Code prepares a compaction about 28K tokens before it applies one
# (measured at windows of 100K and 250K). The nudge comes a few calls before that.
NUDGE_LEAD = 36_000
MIN_WORK = 20_000      # no nudge until this much was added since the last compaction
CEILING_RATIO = 0.96   # stop holding compaction past this share of the window
RENUDGE_TOKENS = 15_000
FRESH_SECONDS = 30 * 60
STALE_TOKENS = 10_000  # work past a checkpoint that the next compaction would lose
MAX_HOLDS = 25
TAIL_BYTES = 600_000   # enough for the last model call, small enough for every tool call
STATE_CHARS = 6_000
HOOK_OUT_CHARS = 9_500  # Claude Code cuts hook output at 10,000 characters
GAP_CHARS = 1_200       # the least room the work after a checkpoint gets in a reload
PENDING_SECONDS = 15 * 60  # how long a launched checkpoint may take to land

GENIE_DIR = STATE_HOME
SESS_DIR = GENIE_DIR / "sessions"
CKPT_RE = re.compile(r"(?:\bgenie(?:\.py)?[\"']?\s+checkpoint\b|python[\w.]*\s+\S*checkpoint\.py\b)")
RECEIPT_RE = re.compile(r"\breceipt: (\d{10,}-[0-9a-f]{8})\b")
RECEIPT_FAILED = "receipt: failed"


# ---------------------------------------------------------------- settings

def local_settings():
    return genie_paths.root() / ".claude" / "settings.local.json"


def window():
    """The window Claude Code was told to use, else the one in .genie/config.json."""
    for w in (read_json(local_settings(), {}).get("autoCompactWindow"),
              genie_paths.config().get("compact_window")):
        if isinstance(w, int) and w >= 100_000:
            return w
    return genie_paths.DEFAULTS["compact_window"]


def nudge_at(w, floor=0):
    """Where the checkpoint nudge starts. `floor` is the context size right
    after the last compaction or the start. Below floor + MIN_WORK there is
    little to save. Never later than the compaction itself."""
    compact = w * COMPACT_RATIO
    return int(min(compact, max(compact - NUDGE_LEAD, floor + MIN_WORK)))


def limits(floor=0):
    w = window()
    return w, nudge_at(w, floor), int(w * CEILING_RATIO)


def looks_like_checkpoint(cmd):
    """The command names genie's checkpoint. That says where a save may sit in
    the recording. It never proves one: `grep "genie checkpoint"` matches too."""
    return "--dry-run" not in cmd and bool(CKPT_RE.search(cmd))


def own_checkpoint(cmd):
    """A command the project named as its checkpoint, in `checkpoint_pattern`.
    It writes no receipt, so a match is all there is to go on."""
    extra = genie_paths.config().get("checkpoint_pattern")
    try:
        return bool(extra and "--dry-run" not in cmd and re.search(extra, cmd))
    except re.error:
        return False


# ---------------------------------------------------------------- transcript

def tail_lines(path, nbytes=TAIL_BYTES):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - nbytes))
            data = fh.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if size > nbytes:
        lines = lines[1:]  # first line is cut
    return lines


def context_tokens(path, after=0):
    """Context size at the last main-chain model call. 0 when unknown.

    `after` drops readings older than that epoch, so a transcript that lags
    behind a compaction does not report the old size.
    """
    for raw in reversed(tail_lines(path)):
        if b'"usage"' not in raw or b'"assistant"' not in raw:
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        if after and to_epoch(d.get("timestamp", "")) < after:
            return 0
        u = (d.get("message") or {}).get("usage") or {}
        total = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                 + u.get("cache_creation_input_tokens", 0) + u.get("output_tokens", 0))
        if total:
            return total
    return 0


# ---------------------------------------------------------------- state

def load_state(sid):
    return read_json(SESS_DIR / f"{sid}.json", {})


def save_state(sid, st):
    try:
        write_json(SESS_DIR / f"{sid}.json", st)
    except OSError:
        pass


def fresh(st, now, soft, tokens=0):
    """True when a checkpoint covers the current stretch of work.
    Both bounds must hold. With one bound, a checkpoint taken at a high
    token count counts as fresh forever and the work after it is lost."""
    ck = st.get("ckpt_time")
    if not ck or ck < st.get("compacted_at", 0):
        return False
    since = max(0, (tokens or st.get("tokens", 0)) - st.get("ckpt_tokens", 0))
    return (now - ck) <= FRESH_SECONDS and since <= STALE_TOKENS


def k(n):
    return f"{n / 1000:.0f}K"


def clock(epoch):
    return time.strftime("%H:%M", time.localtime(epoch or 0))


# ---------------------------------------------------------------- receipts

def file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def response_text(d):
    """What the tool printed, as plain text."""
    def flat(x):
        if isinstance(x, str):
            return [x]
        if isinstance(x, dict):
            return [t for v in x.values() for t in flat(v)]
        return [t for v in x for t in flat(v)] if isinstance(x, list) else []
    return "\n".join(flat(d.get("tool_response")))


def waiting_receipts(sid, d):
    """Receipt files this session may take, oldest first. Its own folder, and
    any receipt whose id the checkpoint printed in this very tool call. The
    second way serves a shell that does not pass the session id on."""
    found = []
    folder = RECEIPTS / session_key(sid)
    if folder.is_dir():
        found += sorted(folder.glob("*.json"))
    text = response_text(d)
    if "receipt: " in text:
        found += [p for m in RECEIPT_RE.finditer(text)
                  if (p := RECEIPTS / "_" / f"{m.group(1)}.json").is_file()]
    return found


def mark_checkpoint(st, d, now, tokens, here, rec=None):
    """Record a checkpoint in the session state. `here` is true when this very
    tool call ran it. Otherwise it was launched earlier, in the background,
    and the launch is where its state was written."""
    launch = st.pop("pending", None) or {}
    if here or now - launch.get("time", 0) > PENDING_SECONDS:
        launch = {"call": d.get("tool_use_id") or "" if here else "", "tokens": tokens,
                  "offset": file_size(d.get("transcript_path", ""))}
    rec = rec or {}
    st["ckpt_time"] = rec.get("time") or now
    st["ckpt_tokens"] = launch.get("tokens") or tokens
    st["nudged_at"] = None
    st["held"] = False
    st["ckpt"] = {"time": st["ckpt_time"], "track": rec.get("track", ""), "topic": rec.get("topic", ""),
                  "call": launch.get("call", ""), "offset": launch.get("offset", 0),
                  "state": bool(rec.get("entry"))}
    if rec.get("track"):
        st["track"] = rec["track"]
    if rec.get("entry"):
        st["saved"] = {"time": st["ckpt_time"], "track": rec["track"], "topic": rec.get("topic", ""),
                       "entry": rec["entry"]}


def claim(sid, st, d, now, tokens):
    """Take every receipt that waits for this session. True when one was taken."""
    cmd = str((d.get("tool_input") or {}).get("command", ""))
    text = response_text(d)
    here = looks_like_checkpoint(cmd)
    launched = now - (st.get("pending") or {}).get("time", 0) <= PENDING_SECONDS
    took = False
    for path in waiting_receipts(sid, d):
        rec = read_json(path, None)
        try:
            path.unlink()
        except OSError:
            continue  # another hook of this session has it
        if isinstance(rec, dict):
            # This call saved it when its id is in this call's output, or when the call names the
            # checkpoint and none was launched earlier. The agent may have piped the id away.
            mine = bool(rec.get("id")) and f"receipt: {rec['id']}" in text
            mark_checkpoint(st, d, now, tokens, mine or (here and not launched), rec)
            took = True
    if took:
        return True
    if own_checkpoint(cmd) or (here and RECEIPT_FAILED in text):
        mark_checkpoint(st, d, now, tokens, True)
        return True
    if here and not launched:
        st["pending"] = {"call": d.get("tool_use_id") or "", "tokens": tokens, "time": now,
                         "offset": file_size(d.get("transcript_path", ""))}
    return False


# ---------------------------------------------------------------- hooks

def emit_context(event, text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                             "additionalContext": text}}))


def on_post_tool(d, st, now):
    tokens = context_tokens(d.get("transcript_path", ""), st.get("compacted_at", 0))
    if tokens:
        st["tokens"] = tokens
        st["floor"] = min(tokens, st.get("floor") or tokens)
    win, soft, _ = limits(st.get("floor", 0))
    st["seen"] = now
    if claim(d.get("session_id"), st, d, now, tokens):
        return
    # A hold can come below the nudge point, because compaction is prepared early. Say so at once.
    if not tokens or (tokens < soft and not st.get("held")) or fresh(st, now, soft, tokens):
        return
    last = st.get("nudged_at")
    if last is not None and tokens < last:
        last = None  # context shrank, so the old mark is from another cycle
    if last is not None and tokens - last < RENUDGE_TOKENS and not st.get("held"):
        return
    st["nudged_at"] = tokens
    st["nudges"] = st.get("nudges", 0) + 1
    held = st.pop("held", False)
    lead = ("Compaction is on hold until you checkpoint."
            if held else f"Auto-compaction runs near {k(win * COMPACT_RATIO)} and waits for a checkpoint.")
    emit_context("PostToolUse", (
        f"GENIE: this session's context is at {k(tokens)} tokens. {lead} "
        "At the next safe point run the genie:checkpoint skill for your track, one call. "
        "Then keep working. Do not stop and do not ask the human about this. "
        "After compaction your STATE.md is reloaded for you."))


def log_line(text):
    try:
        with open(GENIE_DIR / "precompact.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def log_pre_compact(d, st, now, tokens, is_fresh, holds, hold):
    """One line per PreCompact call, so a compaction that lost work can be
    traced to its cause: no checkpoint, a stale one, or the hold cap."""
    ck = st.get("ckpt_time")
    age = f"{(now - ck) / 60:.0f}m" if ck else "-"
    log_line(f"{str(d.get('session_id', ''))[:8]} "
             f"trigger={d.get('trigger')} tokens={tokens} ckpt_tokens={st.get('ckpt_tokens')} "
             f"ckpt_age={age} fresh={is_fresh} holds={holds} -> {'HOLD' if hold else 'pass'}")


def on_pre_compact(d, st, now):
    _, soft, ceiling = limits()
    tokens = context_tokens(d.get("transcript_path", "")) or st.get("tokens", 0)
    claim(d.get("session_id"), st, d, now, tokens)  # a background checkpoint may have landed since the last call
    is_fresh = fresh(st, now, soft, tokens)
    holds = st.get("holds", 0)
    hold = (d.get("trigger") == "auto" and tokens and tokens < ceiling
            and not is_fresh and holds < MAX_HOLDS)
    log_pre_compact(d, st, now, tokens, is_fresh, holds, hold)
    if hold:
        st["holds"] = holds + 1
        st["held"] = True
        st.pop("pass", None)  # an earlier pass led to no compaction, so it marks nothing
        last = st.get("ckpt_tokens")
        why = (f"the last checkpoint was at {k(last)}" if last and st.get("ckpt_time", 0) >= st.get("compacted_at", 0)
               else "no checkpoint yet")
        return 2, (f"genie: holding compaction at {k(tokens)}, {why}. "
                   "Run the genie:checkpoint skill now, then carry on")
    # Claude Code keeps what is written from about here on, word for word. See genie_recall.gap.
    st["pass"] = {"time": now, "offset": file_size(d.get("transcript_path", ""))}
    track = st.get("track")
    since = time.strftime("%H:%M", time.localtime(st["ckpt_time"])) if is_fresh else None
    out = [
        "Keep these in the summary, word for word where you can:",
        "- the human's standing instructions and any /loop prompt in force",
        "- the track slug and the board or bridge channel this session works on",
        "- background jobs still running, with log paths and how to check them",
        "- anything waiting on the human: pushes, submissions, secrets, spending",
    ]
    if since and track:
        out.append(f"- work done after the {since} checkpoint. Do not restate "
                   f"tracks/{track}/STATE.md, it is reloaded after compaction.")
    else:
        out.append("- no recent checkpoint exists, so keep full working detail: "
                   "files touched, numbers, failed attempts and the next step")
    print("\n".join(out))
    return 0, ""


def newest_checkpoint(track, root):
    p = Path(root) / "tracks" / track / "STATE.md"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    parts = text.split("<!-- checkpoint -->")
    return parts[1].strip()[:STATE_CHARS] if len(parts) > 1 else ""


def lab_briefs():
    try:
        import genie_lab
        return [genie_lab.one_line(lab.parent.name) for lab in genie_lab.labs()]
    except Exception:
        return []


def open_gate_line():
    try:
        import genie_lab
        ids = [g["id"] for home in genie_lab.gate_homes() for g in genie_lab.gates(home)[1]
               if g["status"] == "open"]
        return [f"GENIE: {len(ids)} gates wait on the human: {', '.join(ids[:8])}"] if ids else []
    except Exception:
        return []


def digest_lines(cfg):
    out = []
    if cfg["hooks"].get("digest"):
        try:
            import vault
            out.append(vault.digest(int(cfg.get("digest_chars") or 0) or 6000))
        except Exception as exc:
            out.append(f"GENIE: the notes digest failed ({exc})")
    if cfg["hooks"].get("bridge"):
        try:
            r = subprocess.run([sys.executable, str(Path(__file__).with_name("bridge.py")), "brief"],
                               capture_output=True, text=True, timeout=9)
            out.append(r.stdout.strip())
        except Exception:
            pass
    return [x for x in out if x]


def state_block(st):
    """This session's own last state, else the newest one on file for its track."""
    saved, track = st.get("saved"), st.get("track")
    if saved:
        entry = saved["entry"]
        cut = (f"\n... cut at {STATE_CHARS} characters. The rest is in tracks/{saved['track']}/STATE.md"
               if len(entry) > STATE_CHARS else "")
        return (f"Track: {saved['track']}. Your checkpoint of {clock(saved['time'])} ({saved['topic']}), "
                f"word for word:\n\n{entry[:STATE_CHARS]}{cut}")
    body = newest_checkpoint(track, genie_paths.checkout()) if track else ""
    if body:
        return (f"Track: {track}. This session saved no state of its own. Newest checkpoint in "
                f"tracks/{track}/STATE.md, which may be another session's:\n\n{body}")
    return "No track checkpoint is on file for this session. Work from the summary above."


def gap_block(st, path, prev, room):
    """The work between the last checkpoint and the messages Claude Code kept."""
    import genie_recall
    ck = st.get("ckpt") or {}
    if ck.get("time", 0) < prev:
        ck = {}  # saved before the compaction before this one, so it covers nothing here
    went = st.get("pass") or {}
    found = genie_recall.gap(path, call=ck.get("call", ""), ckpt_time=ck.get("time", 0),
                             hint=ck.get("offset", 0) or st.get("compact_offset", 0), after=prev + 30,
                             stop=went.get("offset", 0) if went.get("time", 0) > prev else 0,
                             root=st.get("root", ""))
    log_line(f"{str(st.get('sid', ''))[:8]} reload ckpt={clock(ck['time']) if ck else '-'} "
             f"end={found['how']} lines={len(found['items'])} cut={found['cut']}")
    if not found["items"]:
        return ("Nothing happened between that checkpoint and the messages you still have. "
                "The state is complete.") if ck and not found["cut"] else ""
    head = ("Work after that checkpoint and before the messages you still have, from the recording:" if ck
            else "No checkpoint covers this stretch. Work before the messages you still have, from the recording:")
    tail = ["These lines are newer than the state. Where they disagree, they win."] if ck else []
    if found["how"] == "pass":
        tail.append("The last few of these lines may repeat messages you still have.")
    elif found["how"] == "end":
        tail.append("Some of these lines may repeat messages you still have.")
    if found["cut"]:
        tail.append("The recording is large, so the oldest part of this stretch is not listed.")
    tail.append("Full text of a line: `genie recall --at <number>`. Search the recording: `genie recall <words>`.")
    return "\n".join([head, genie_recall.render(found["items"], room - len(head) - sum(len(t) + 1 for t in tail) - 2)]
                     + tail)


def on_session_start(d, st, now, cfg, pre):
    digest, briefs, gates = pre
    lab_note = ["GENIE: if you work on one of these tracks, that line is where you are. "
                "Do not redo work the lab already records."] if briefs else []
    source, alive = d.get("source"), cfg["hooks"].get("keep_alive")
    path = d.get("transcript_path") or st.get("transcript") or ""
    if alive:
        claim(d.get("session_id"), st, d, now, st.get("tokens", 0))
    if source != "compact":
        mine = []
        if alive and source == "resume" and st.get("ckpt"):
            ck = st["ckpt"]
            mine = [f"GENIE: resumed. Your last checkpoint was at {clock(ck['time'])} ({ck['topic']}).",
                    state_block(st),
                    "`genie recall <words>` searches this session's recording. "
                    "`genie recall --gap` lists the work after that checkpoint.", ""]
        return "\n".join(mine + digest + briefs + lab_note + gates)
    prev = st.get("compacted_at", 0)
    st["compacted_at"] = now
    st.pop("floor", None)
    st["compactions"] = st.get("compactions", 0) + 1
    st["nudged_at"] = None
    st["holds"] = 0
    st["held"] = False
    if not alive:
        return "\n".join(digest + briefs + gates)
    track = st.get("track")
    lines = ["GENIE: this session was just compacted. Carry on with the work now.",
             "Do not wait for the human unless a gate applies (push, submission, secret, spending).",
             "Restart any `genie board wait` or `genie bridge wait` you had running.",
             state_block(st)]
    ck = st.get("ckpt") or {}
    if ck and not ck.get("state") and ck.get("time", 0) >= prev:
        lines.append(f"Your last checkpoint ({clock(ck['time'])}, {ck['topic'] or 'no topic'}) "
                     "filed notes only, with no track state.")
    after = []
    if track:
        try:
            import genie_lab
            if (genie_lab.lab_dir(track) / "genie.toml").exists():
                after.append("\nWhere the lab stands:\n" + genie_lab.render_next(track))
        except Exception:
            pass
    else:
        after.extend(briefs)
    after.extend(gates)
    used = len("\n".join(lines + after))
    try:
        block = gap_block(st, path, prev, max(GAP_CHARS, HOOK_OUT_CHARS - used - 40))
    except Exception as exc:  # the state above must arrive even when the recording cannot be read
        block = f"GENIE: could not read the recording for work after the checkpoint ({exc})."
    if block:
        lines += ["", block]
    st["compact_offset"] = file_size(path)
    # The checkpoint matters most after a compaction, so the digest gets what is left.
    text = "\n".join(lines + after)
    room = HOOK_OUT_CHARS - len(text)
    extra = "\n".join(digest)
    return text + ("\n\n" + extra[:room - 2] if digest and room > 400 else "")


def on_pre_tool(d):
    try:
        import genie_lab
        msg = genie_lab.rule_check(d)
    except Exception:
        return 0, ""  # a broken rule must never stop a session
    return (2, msg) if msg else (0, "")


def cmd_hook(_args):
    if not genie_paths.enabled():
        return 0
    try:
        d = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    sid = d.get("session_id")
    if not sid:
        return 0
    cfg = genie_paths.config()
    on = cfg["hooks"]
    event = d.get("hook_event_name")
    if event == "PreToolUse":  # rules bind subagents too, and need no session state
        code, err = on_pre_tool(d) if on.get("rules") else (0, "")
        if err:
            print(err, file=sys.stderr)
        return code
    if d.get("agent_id"):
        return 0  # a subagent has its own context
    now = time.time()
    code, err, text = 0, "", ""
    # The slow part of a session start reads no session state, so it runs before the lock.
    pre = (digest_lines(cfg), lab_briefs(), open_gate_line()) if event == "SessionStart" else None
    # Claude Code runs the hooks of parallel tool calls at once. One at a time per session,
    # or two of them read the same state and the later write drops the earlier one.
    with Lock(SESS_DIR / f"{session_key(sid)}.lock"):
        st = load_state(sid)
        st["sid"] = sid
        if d.get("transcript_path"):
            st["transcript"] = d["transcript_path"]
        st["root"] = str(genie_paths.checkout())
        try:
            if event == "PostToolUse" and on.get("keep_alive"):
                on_post_tool(d, st, now)
            elif event == "PreCompact" and on.get("keep_alive"):
                code, err = on_pre_compact(d, st, now)
            elif event == "SessionStart":
                text = on_session_start(d, st, now, cfg, pre)
        except Exception as exc:  # a broken hook must never stop a session
            code, err = 0, f"genie hook error: {exc}"
        save_state(sid, st)
    if text:
        sys.stdout.buffer.write((text[:HOOK_OUT_CHARS] + "\n").encode("utf-8"))
    if err:
        print(err, file=sys.stderr)
    return code


# ---------------------------------------------------------------- set up

IGNORE = "board.git/\n.locks/\nsecond-opinion/\n*.tmp\n*.tmp/\n"
OVERVIEW = """---
date: {today}
updated: {today}
tags: [hub]
type: hub
summary: Status board and links. Start here.
---
# Overview

## Status board

| Track | Owner | Best local | Best outside | Next |
|---|---|---|---|---|

## Findings

## Where things are

- Decisions: [[decisions]]
- Findings: one note each in `{notes}/findings/`
- Track state for handoff: `tracks/<slug>/STATE.md`, written by the checkpoint
"""
DECISIONS = """---
date: {today}
updated: {today}
tags: [decisions]
type: log
summary: Dated decisions with the reason and the rejected option. Newest at the bottom.
---
# Decisions

One row per decision. Never rewrite an old row. A reversal is a new row.

Back to [[{hub}]].

| Date | Decision | Why | Who |
|---|---|---|---|
"""
TRACK_README = """# {title}

Back to [[{hub}]].

## Goal
TODO: what done looks like, in one or two lines.

## How it is judged
TODO: the test, score or review that decides.

## Limits
TODO: what this track may not do.
"""


def put(path, text, made):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    made.append(path)


def set_window(tokens):
    path = local_settings()
    s = read_json(path, {}) if path.exists() else {}
    if path.exists() and not isinstance(s, dict):
        return "left alone, it is not a JSON object"
    if path.exists() and not s and path.read_text(encoding="utf-8").strip() not in ("", "{}"):
        return "left alone, it does not parse"
    if tokens is None:
        s.pop("autoCompactWindow", None)
    else:
        s["autoCompactWindow"] = max(100_000, tokens)
    write_json(path, s)
    return "removed" if tokens is None else f"{max(100_000, tokens)}"


def cmd_init(args):
    top = genie_paths.root()
    made, today = [], time.strftime("%Y-%m-%d")
    fresh_cfg = not (top / genie_paths.CONFIG).exists()
    if fresh_cfg:
        cfg = {k: genie_paths.DEFAULTS[k] for k in ("version", "compact_window", "hooks", "notes", "hub", "watch")}
        if args.window:
            cfg["compact_window"] = max(100_000, args.window)
        write_json(top / genie_paths.CONFIG, cfg)
        made.append(top / genie_paths.CONFIG)
    cfg = genie_paths.config()
    put(top / ".genie" / ".gitignore", IGNORE, made)
    notes = top / cfg["notes"]
    put(notes / f"{cfg['hub']}.md", OVERVIEW.format(today=today, notes=cfg["notes"]), made)
    put(notes / "decisions.md", DECISIONS.format(today=today, hub=cfg["hub"]), made)
    put(notes / "findings" / ".gitkeep", "", made)
    print(f"genie init in {top}")
    for p in made:
        print(f"  wrote {p.relative_to(top).as_posix()}")
    if not made:
        print("  nothing to write, this project is already set up")
    if not args.no_window:
        print(f"  .claude/settings.local.json autoCompactWindow: {set_window(cfg['compact_window'])}")
    _, soft, ceiling = limits()
    print(f"Checkpoint nudge at {k(soft)}, compaction near {k(window() * COMPACT_RATIO)}, forced past {k(ceiling)}.")
    print("Hooks work now. The window applies to sessions started or resumed after this.")
    print("Next: `genie track add <slug>` for each piece of work, then commit .genie, notes and tracks.")
    return 0


def cmd_on(args):
    cfgp = genie_paths.root() / genie_paths.CONFIG
    raw = read_json(cfgp, None)
    if not isinstance(raw, dict):
        print("run `genie init` first", file=sys.stderr)
        return 1
    raw.setdefault("hooks", {})["keep_alive"] = args.cmd == "on"
    if args.cmd == "on" and args.window:
        raw["compact_window"] = max(100_000, args.window)
    write_json(cfgp, raw)
    if args.cmd == "off":
        print(f"keep-alive off. autoCompactWindow {set_window(None)}.")
        return 0
    print(f"keep-alive on. autoCompactWindow {set_window(window() if not args.window else args.window)}.")
    print("The window applies to sessions started or resumed after this.")
    return 0


def cmd_track(args):
    top = genie_paths.checkout()
    if not genie_paths.enabled():
        print("run `genie init` first", file=sys.stderr)
        return 1
    if args.action == "list":
        for p in sorted((top / "tracks").glob("*/")):
            print("  " + p.name)
        return 0
    slug = args.slug or ""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug) or slug == "none":
        print("a track slug is lowercase letters, digits and dashes", file=sys.stderr)
        return 1
    cfg, made = genie_paths.config(), []
    title = (args.title or slug.replace("-", " ").capitalize()).replace("|", "/")
    put(top / "tracks" / slug / "README.md", TRACK_README.format(title=title, hub=cfg["hub"]), made)
    hub = genie_paths.hub_note(top)
    text = hub.read_text(encoding="utf-8")
    if f"tracks/{slug}/" not in text:
        row = f"| [[tracks/{slug}/README\\|{title}]] | {args.owner or ''} |  |  |  |"
        lines = text.splitlines()
        at = next((i for i, l in enumerate(lines) if re.match(r"^##\s+Status board\s*$", l)), None)
        if at is None:
            print(f"{hub} has no `## Status board` section, so no row was added", file=sys.stderr)
        else:
            end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith("#")), len(lines))
            last = max((i for i in range(at + 1, end) if lines[i].startswith("|")), default=at)
            lines.insert(last + 1, row)
            hub.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            made.append(hub)
    for p in made:
        print(f"  wrote {p.relative_to(top).as_posix()}")
    print(f"Start its session with: /genie:track {slug}")
    return 0


def cmd_doctor(_args):
    import shutil
    top, bad = genie_paths.root(), 0

    def line(ok, text, must=True):
        nonlocal bad
        bad += 0 if ok or not must else 1
        print(f"  {'ok  ' if ok else ('FAIL' if must else 'note')} {text}")
    line(True, f"python {sys.version.split()[0]} at {sys.executable}")
    line(shutil.which("git") is not None, "git on PATH")
    line(genie_paths.enabled(), f"project opted in ({top / genie_paths.CONFIG})")
    if genie_paths.enabled():
        raw = read_json(top / genie_paths.CONFIG, None)
        line(isinstance(raw, dict), "config.json parses")
        cfg = genie_paths.config()
        line(genie_paths.hub_note(top).exists(), f"hub note {cfg['notes']}/{cfg['hub']}.md")
        w = read_json(local_settings(), {}).get("autoCompactWindow")
        line(isinstance(w, int), f"autoCompactWindow {w or 'not set, so compaction happens late'}", must=False)
        seen = list(SESS_DIR.glob("*.json"))
        line(bool(seen), f"{len(seen)} sessions have run the hooks on this machine", must=False)
    line((HOME / ".claude" / "sessions").is_dir(), "~/.claude/sessions exists, so `genie watch` can see sessions", must=False)
    for tool, why in (("codex", "second opinions"), ("gh", "scout lanes that read GitHub")):
        line(shutil.which(tool) is not None, f"{tool} on PATH ({why})", must=False)
    print("all required checks pass" if not bad else f"{bad} required checks failed")
    return 1 if bad else 0


# ---------------------------------------------------------------- status

def project_dirs():
    """Transcript folders of every worktree of this project. Claude Code names
    a folder after the session's start directory."""
    base = HOME / ".claude" / "projects"
    out = []
    for tree in worktrees():
        d = base / re.sub(r"[^A-Za-z0-9]", "-", str(tree))
        if d.is_dir() and d not in out:
            out.append(d)
    return out


def scan_session(path):
    title, ckpt_ts, compactions = "", "", 0
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                if b'"custom-title"' in raw or b'"ai-title"' in raw:
                    try:
                        d = json.loads(raw)
                        title = d.get("customTitle") or d.get("aiTitle") or title
                    except Exception:
                        pass
                elif b'"compact_boundary"' in raw:
                    compactions += 1
                elif (b"checkpoint.py" in raw or b"genie checkpoint" in raw) and b'"tool_use"' in raw:
                    m = re.search(rb'"timestamp":"([^"]+)"', raw)
                    if m:
                        ckpt_ts = m.group(1).decode()
    except OSError:
        pass
    last_kind, last_ts = "", ""
    for raw in reversed(tail_lines(path)):
        if b'"timestamp"' not in raw:
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue
        if d.get("isSidechain") or d.get("type") not in ("assistant", "user"):
            continue
        last_ts = d.get("timestamp", "")
        content = (d.get("message") or {}).get("content")
        tool = isinstance(content, list) and any(
            isinstance(c, dict) and c.get("type") in ("tool_use", "tool_result") for c in content)
        last_kind = "tool" if tool else d.get("type")
        break
    return title, ckpt_ts, compactions, last_kind, last_ts


def to_epoch(ts):
    if not ts:
        return 0
    try:
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0


def ago(epoch, now):
    if not epoch:
        return "never"
    m = int((now - epoch) / 60)
    if m < 1:
        return "now"
    return f"{m} min" if m < 90 else f"{m / 60:.1f} h"


def table(headers, rows):
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)] if rows else [len(h) for h in headers]
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for r in rows:
        print(fmt.format(*[str(x) for x in r]))


def worktrees():
    try:
        out = subprocess.run(["git", "-C", str(genie_paths.root()), "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, timeout=10).stdout
        return [Path(l[9:]) for l in out.splitlines() if l.startswith("worktree ")] or [genie_paths.root()]
    except Exception:
        return [genie_paths.root()]


def first_sentence(text, cap):
    text = re.sub(r"\s+", " ", text).strip()
    cut = re.split(r"(?<=[.!?]) ", text, maxsplit=1)[0]
    return cut if len(cut) <= cap else cut[: cap - 3] + "..."


def track_rows(now):
    best = {}
    for root in worktrees():
        for p in root.glob("tracks/*/STATE.md"):
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if p.parent.name not in best or mt > best[p.parent.name][0]:
                best[p.parent.name] = (mt, p)
    rows = []
    for track, (mt, p) in sorted(best.items()):
        parts = p.read_text(encoding="utf-8", errors="replace").split("<!-- checkpoint -->")
        body = parts[1] if len(parts) > 1 else ""
        sec = {m.group(1): m.group(2) for m in
               re.finditer(r"^## (Next|Blocked)\n(.*?)(?=^## |\Z)", body, re.S | re.M)}
        rows.append((track, ago(mt, now), first_sentence(sec.get("Next", ""), 90),
                     first_sentence(sec.get("Blocked", ""), 60)))
    return rows


def cmd_status(args):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    now = time.time()
    w, soft, _ = limits()
    if not genie_paths.enabled():
        print("This project has not opted in. Run `genie init`.")
        return 1
    alive = genie_paths.config()["hooks"].get("keep_alive")
    print(f"keep-alive {'ON' if alive else 'OFF'}. Nudge at {k(soft)}, compact near {k(w * COMPACT_RATIO)}.\n")
    rows = []
    files = [p for d in project_dirs() for p in d.glob("*.jsonl")]
    for p in sorted(files, key=lambda x: -x.stat().st_mtime):
        mt = p.stat().st_mtime
        if now - mt > args.hours * 3600:
            continue
        title, ckpt_ts, compactions, kind, last_ts = scan_session(p)
        st = load_state(p.stem)
        tokens = context_tokens(p, st.get("compacted_at", 0))
        last = to_epoch(last_ts) or mt
        quiet = now - last
        if quiet < 180:
            state = "working"
        elif kind == "assistant":
            state = "YOUR TURN"
        else:
            state = "quiet"
        # A session that ran the hooks knows its saved checkpoints. For any other, a
        # command that names the checkpoint is the best guess there is.
        ck = st.get("ckpt_time", 0) if st else to_epoch(ckpt_ts)
        flag = " !" if tokens >= nudge_at(w, st.get("floor", 0)) else ""
        size = k(tokens) + flag if tokens else "compacted"
        rows.append(((title or p.stem[:8])[:34], size, state, ago(last, now),
                     ago(ck, now), st.get("track", ""), compactions))
    print("SESSIONS")
    table(["session", "context", "state", "last move", "checkpoint", "track", "compacts"], rows)
    print("\nTRACKS")
    table(["track", "checkpoint", "next", "blocked"], track_rows(now))
    try:
        import genie_lab
        wishes = genie_lab.wish_rows()
    except Exception:
        wishes = []
    if wishes:
        print("\nLABS")
        table(["track", "stage", "best T2", "best T3", "ideas open", "gates", "next job"], wishes)
    print("\n'YOUR TURN' means the agent's last message was text and it has been quiet 3+ min.")
    return 0


def cmd_window(args):
    import genie_window
    files = genie_window.every_transcript() if args.all else [p for d in project_dirs() for p in d.glob("*.jsonl")]
    now = read_json(local_settings(), {}).get("autoCompactWindow")
    return genie_window.report(args, files, now if isinstance(now, int) else None)


def lab_parser(sub):
    """Lab subcommands. The work is in genie_lab.py."""
    def add(name, fn_name, **defaults):
        p = sub.add_parser(name)
        p.add_argument("--track", default=os.environ.get("GENIE_TRACK"))
        p.add_argument("--worker")
        p.set_defaults(lab_fn=fn_name, **defaults)
        return p

    p = add("wish", "cmd_wish")
    p.add_argument("problem")
    p.add_argument("--better", choices=["lower", "higher"], required=True)
    p = add("adopt", "cmd_adopt")
    p.add_argument("--better", choices=["lower", "higher"], required=True)
    add("next", "cmd_next")
    add("preflight", "cmd_preflight")
    p = add("score", "cmd_score")
    p.add_argument("--tier", choices=["T0", "T1", "T2", "T3"], required=True)
    p.add_argument("--entry", required=True)
    p.add_argument("--idea")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--base", action="store_true", help="this entry is the base to beat")
    p.add_argument("--noise", action="store_true", help="record the spread as this tier's noise")
    p.add_argument("--unfrozen", action="store_true", help="score although the scorer changed")
    p.add_argument("--note", default="")
    p = add("log", "cmd_log")
    p.add_argument("--tier", choices=["T0", "T1", "T2", "T3"], required=True)
    p.add_argument("--score", type=float)
    p.add_argument("--verdict", required=True)
    p.add_argument("--idea")
    p.add_argument("--evidence")
    p.add_argument("--ref")
    p.add_argument("--note")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--spread", type=float)
    p.add_argument("--base-commit")
    for t in ("edit", "build", "score", "wait"):
        p.add_argument(f"--t-{t}", type=float)
    for name in ("keep", "reject", "supersede"):
        p = add(name, "cmd_decide", decision="superseded" if name == "supersede" else name)
        p.add_argument("id")
        p.add_argument("--evidence")
        p.add_argument("--note")
        p.add_argument("--force", action="store_true")
    add("scouted", "cmd_scouted")
    p = add("base", "cmd_base")
    p.add_argument("commit")
    p = add("time", "cmd_time")
    p.add_argument("--last", type=int, default=10)
    p = add("ledger", "cmd_ledger")
    p.add_argument("--last", type=int, default=20)
    p = add("golden", "cmd_golden")
    p.add_argument("action", nargs="?", default="replay", choices=["replay", "add", "remove"])
    p.add_argument("entry", nargs="?")
    p.add_argument("--score", type=float)
    p.add_argument("--tier", choices=["T0", "T1", "T2"])
    p.add_argument("--source", default="published")
    p = add("order", "cmd_order")
    p.add_argument("entries", nargs="+", help="worst first, best last")
    p.add_argument("--tier", choices=["T0", "T1", "T2"], default="T2")
    add("freeze", "cmd_freeze")
    p = add("idea", "cmd_idea")
    p.add_argument("action", choices=["add", "list", "start", "set"])
    p.add_argument("text", nargs="?")
    p.add_argument("--rank", type=float)
    p.add_argument("--source")
    p.add_argument("--test")
    p.add_argument("--files")
    p.add_argument("--status")
    p = add("brief", "cmd_brief")
    p.add_argument("id")
    p.add_argument("--role", choices=["solver", "scorer"], default="solver")
    p.add_argument("--worktree")
    p.add_argument("--force", action="store_true")
    p = add("gate", "cmd_gate")
    p.add_argument("action", choices=["add", "list", "done"])
    p.add_argument("text", nargs="?")
    p.add_argument("--kind", default="other")
    p.add_argument("--standing", action="store_true", help="ask for a lasting approval, not one action")
    p.add_argument("--answer")
    p.add_argument("--all", action="store_true")
    for name in ("lock", "unlock", "locks"):
        p = add(name, "cmd_lock", action=name)
        if name != "locks":
            p.add_argument("name")
        p.add_argument("--wait", type=float, default=0)
        p.add_argument("--ttl", type=float, default=0)
        p.add_argument("--force", action="store_true")


def run_lab(args):
    import genie_lab
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        return getattr(genie_lab, args.lab_fn)(args)
    except genie_lab.GenieError as exc:
        print(f"genie: {exc}", file=sys.stderr)
        return 1


PASS = {"bridge": "bridge", "board": "board", "watch": "genie_watch", "dash": "genie_dash",
        "vault": "vault", "checkpoint": "checkpoint", "second-opinion": "second_opinion",
        "recall": "genie_recall"}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in PASS:
        import importlib
        return importlib.import_module(PASS[argv[0]]).main(argv[1:]) or 0
    if not argv or argv[0] in ("help", "-h", "--help"):
        print(__doc__.strip())
        return 0
    ap = argparse.ArgumentParser(prog="genie")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hook").set_defaults(fn=cmd_hook)
    p = sub.add_parser("init")
    p.add_argument("--window", type=int, default=0, help="compaction window in tokens")
    p.add_argument("--no-window", action="store_true", help="leave .claude/settings.local.json alone")
    p.set_defaults(fn=cmd_init)
    for name in ("on", "off"):
        p = sub.add_parser(name)
        p.add_argument("--window", type=int, default=0)
        p.set_defaults(fn=cmd_on)
    p = sub.add_parser("track")
    p.add_argument("action", choices=["add", "list"])
    p.add_argument("slug", nargs="?")
    p.add_argument("title", nargs="?")
    p.add_argument("--owner")
    p.set_defaults(fn=cmd_track)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("status")
    p.add_argument("--hours", type=float, default=12)
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("window", help="what compaction window is worth it")
    p.add_argument("--all", action="store_true", help="measure every project on this machine")
    p.add_argument("--days", type=float, default=30)
    p.add_argument("--model", help="one model id, else the ones in the transcripts")
    p.add_argument("--floor", type=int, help="context right after a compaction, instead of the measured one")
    p.add_argument("--growth", type=int, help="tokens added per request, instead of the measured one")
    p.add_argument("--price", help="read,write5m,write1h,output in dollars per million tokens")
    p.set_defaults(fn=cmd_window)
    lab_parser(sub)
    # Python 3.11 does not match an optional positional that comes after an
    # option, as in `genie gate add --kind push "text"`. Take it from the rest.
    args, rest = ap.parse_known_args(argv)
    for name in ("slug", "title", "text", "entry"):
        if rest and not rest[0].startswith("-") and getattr(args, name, "") is None:
            setattr(args, name, rest.pop(0))
    if rest:
        ap.error("unrecognized arguments: " + " ".join(rest))
    if hasattr(args, "lab_fn"):
        return run_lab(args)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
