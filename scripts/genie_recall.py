#!/usr/bin/env python3
"""The session recording, read back.

Claude Code writes every message and tool result of a session to one JSONL
file. A compaction replaces most of the chat with a summary. The file stays.

    genie recall <words>          search this session's recording, newest first
    genie recall --at <number>    one entry in full. --skip and --chars page it
    genie recall --gap            everything after the last checkpoint

    --session <id> | --file <path>   another recording
    --n <count>                      hits to show, 8 by default
    --all                            include the work of subagents

Each line starts with @<number>, the entry's place in the file. Recordings
are deleted after `cleanupPeriodDays`, 30 days by default.
"""
import argparse
import calendar
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genie_paths  # noqa: E402
from genie_paths import STATE_HOME, read_json  # noqa: E402

SLACK = 1_000_000        # read this far before a remembered place, so its entry is whole
SCAN_BYTES = 16_000_000  # the most a hook reads. A recording can pass 90 MB
MSG_CHARS = 1_200
SAID_CHARS = 300
CALL_CHARS = 140
RESULT_CHARS = 90
NOISE = ("<local-command", "<command-name>", "<command-message>", "<command-args>", "<bash-input>",
         "<bash-stdout>", "<user-prompt-submit-hook>")
REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
QUIET_TOOLS = {"Edit", "Write", "Read", "NotebookEdit"}  # the path says it all


def to_epoch(ts):
    try:
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0


def read_rows(path, start=0, limit=0):
    """(offset, entry) for every whole line from byte `start`, and whether the
    read began later than asked because of `limit`."""
    rows, cut = [], False
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if start > size:
                start = 0          # the file is not the one we remembered
            if limit and size - start > limit:
                start, cut = size - limit, True
            if start:
                fh.seek(start - 1)
                if fh.read(1) != b"\n":
                    start += len(fh.readline())   # drop the line we landed inside
            pos = start
            for raw in fh:
                at, pos = pos, pos + len(raw)
                if raw.strip():
                    try:
                        rows.append((at, json.loads(raw)))
                    except Exception:
                        pass
    except OSError:
        pass
    return rows, cut


def squash(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def clip(text, cap):
    text = squash(text)
    return text if len(text) <= cap else text[: cap - 3] + "..."


def first_line(text):
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def blocks(row):
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if b.get("type") == "text" else f"[{b.get('type')}]"
                         for b in content if isinstance(b, dict))
    return "" if content is None else json.dumps(content)


def message_text(row):
    """What a person or another agent wrote. Empty for harness bookkeeping."""
    text = "\n".join(b.get("text", "") for b in blocks(row) if b.get("type") == "text")
    text = REMINDER_RE.sub("", text).strip()
    return "" if text.startswith(NOISE) else text


def call_head(name, inp, root=""):
    inp = inp if isinstance(inp, dict) else {}
    if name in ("Bash", "PowerShell"):
        return first_line(inp.get("command"))
    if name in QUIET_TOOLS:
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        # A path inside the project reads shorter from its root.
        return path[len(root):].lstrip("/\\") if root and path.startswith(root) else path
    if name == "Agent":
        return str(inp.get("description") or first_line(inp.get("prompt")))
    if name == "SendMessage":
        return f"to {inp.get('to')}: {first_line(inp.get('message'))}"
    return json.dumps(inp)


def main_chain(row, everything=False):
    return (row.get("type") in ("user", "assistant") and not row.get("isMeta")
            and not row.get("isCompactSummary") and (everything or not row.get("isSidechain")))


# ---------------------------------------------------------------- the gap

def find_call(rows, call):
    for i, (_, row) in enumerate(rows):
        if row.get("type") == "assistant" and any(
                b.get("type") == "tool_use" and b.get("id") == call for b in blocks(row)):
            return i
    return None


def gap(path, call="", ckpt_time=0, hint=0, after=None, stop=0, root=""):
    """The work after a checkpoint that the kept messages do not cover.

    call        tool_use id of the checkpoint command, when known
    ckpt_time   when the checkpoint was saved, used when `call` is not found
    hint        a byte offset at or before the checkpoint, 0 when unknown
    after       only a compaction newer than this epoch is the current one.
                None means there is none: read to the end of the file.
    stop        the file's size when PreCompact last let a compaction through
    root        the project folder. File paths under it are shown from there

    With neither `call` nor `ckpt_time` the stretch starts at the compaction
    before this one. Where it ends is in "how":

      head      the compaction's own record names the first kept message. Exact.
      boundary  the record names none, so nothing was kept.
      pass      Claude Code writes that record after the SessionStart hook has
                run, so a hook sees no record. Everything written after
                PreCompact let the compaction through is kept (0 exceptions in
                57 compactions), and the kept part starts up to 7 entries
                before that, so the last few lines may repeat kept messages.
      end       neither is known. Every line may be one Claude Code kept.
    """
    rows, cut = read_rows(path, max(0, hint - SLACK) if hint else 0, SCAN_BYTES)
    return gap_in(rows, cut, call, ckpt_time, after, stop, root)


def gap_in(rows, cut=False, call="", ckpt_time=0, after=None, stop=0, root=""):
    """`gap` over entries already read."""
    bounds = [i for i, (_, r) in enumerate(rows) if r.get("subtype") == "compact_boundary"]
    b = None
    if after is not None and bounds and to_epoch(rows[bounds[-1]][1].get("timestamp", "")) >= after:
        b = bounds[-1]
    earlier = [i for i in bounds if b is None or i < b]

    start = None
    if call:
        i = find_call(rows, call)
        start = None if i is None else i + 1
    if start is None and ckpt_time:
        start = next((i for i, (_, r) in enumerate(rows)
                      if r.get("timestamp") and to_epoch(r["timestamp"]) > ckpt_time), len(rows))
    if start is None:
        if earlier and (after is None or b is not None):
            start = earlier[-1] + 1
        elif after:
            start = next((i for i, (_, r) in enumerate(rows)
                          if r.get("timestamp") and to_epoch(r["timestamp"]) > after), len(rows))
        else:
            start = 0

    end, how = len(rows), "end"
    if b is not None and b >= start:
        end, how = b, "boundary"
        head = ((rows[b][1].get("compactMetadata") or {}).get("preservedSegment") or {}).get("headUuid")
        if head:
            h = next((i for i in range(b) if rows[i][1].get("uuid") == head), None)
            # A kept part that begins before the checkpoint covers everything after it.
            end, how = (start if h is None or h < start else h), "head"
    elif stop:
        end = max(start, next((i for i, (at, _) in enumerate(rows) if at >= stop), len(rows)))
        how = "pass"
    return {"items": items(rows, start, end, root=root), "how": how, "cut": cut}


def items(rows, start, end, everything=False, root=""):
    """(offset, kind, text) for each message and tool call in rows[start:end]."""
    results = {}
    for _, row in rows[start:]:
        if row.get("type") == "user":
            for blk in blocks(row):
                if blk.get("type") == "tool_result":
                    results[blk.get("tool_use_id")] = (result_text(blk.get("content")), blk.get("is_error"))
    out = []
    for at, row in rows[start:end]:
        if not main_chain(row, everything):
            continue
        if row.get("type") == "user":
            text = message_text(row)
            if text.startswith("<task-notification"):
                m = re.search(r"<summary>(.*?)</summary>", text, re.S)
                out.append((at, "notice", m.group(1) if m else text))
            elif text:
                out.append((at, "message", text))
            continue
        for blk in blocks(row):
            if blk.get("type") == "text" and blk.get("text", "").strip():
                out.append((at, "said", blk["text"].strip()))
            elif blk.get("type") == "tool_use":
                name = blk.get("name", "tool")
                line = f"{name}: {clip(call_head(name, blk.get('input'), root), CALL_CHARS)}"
                res, bad = results.get(blk.get("id"), ("", False))
                if bad:
                    line += " -> ERROR " + clip(first_line(res), RESULT_CHARS)
                elif res and name not in QUIET_TOOLS:
                    line += " -> " + clip(first_line(res), RESULT_CHARS)
                out.append((at, "call", line))
    return out


def one_line(item, msg_chars=MSG_CHARS):
    at, kind, text = item
    if kind == "message":
        return f"- MESSAGE: {clip(text, msg_chars)}  @{at}"
    if kind == "notice":
        return f"- NOTICE: {clip(text, SAID_CHARS)}  @{at}"
    if kind == "said":
        return f"- SAID: {clip(text, SAID_CHARS)}  @{at}"
    return f"- {text}  @{at}"


def render(found, room):
    """The items as lines that fit `room` characters. Messages from people
    always stay. Of the rest the newest stay, and the count left out is said."""
    lines = [one_line(it) for it in found]
    if sum(len(x) + 1 for x in lines) <= room:
        return "\n".join(lines)
    note = "- ... {} earlier lines left out. `genie recall --gap` lists them all."
    for cap in (MSG_CHARS, 600, 300, 150, 80):
        keep = {i: one_line(it, cap) for i, it in enumerate(found) if it[1] == "message"}
        if sum(len(x) + 1 for x in keep.values()) <= room * 0.6:
            break
    left = room - sum(len(x) + 1 for x in keep.values()) - len(note) - 8
    for i in range(len(found) - 1, -1, -1):
        if i in keep:
            continue
        left -= len(lines[i]) + 1
        if left < 0:
            break
        keep[i] = lines[i]
    out = [keep[i] for i in sorted(keep)]
    while sum(len(x) + 1 for x in out) + len(note) + 8 > room and len(out) > 1:
        out.pop(0)
    return "\n".join([note.format(len(found) - len(out))] + out)


# ---------------------------------------------------------------- the command

def out(text=""):
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))


def die(msg):
    sys.stderr.write("genie recall: " + msg + "\n")
    return 1


def session_state(args):
    """(state, error). The state file of the session whose recording is wanted."""
    sid = args.session or os.environ.get("CLAUDE_CODE_SESSION_ID")
    folder = STATE_HOME / "sessions"
    if sid:
        st = read_json(folder / f"{sid}.json", None)
        return (st, "") if st else (None, f"no session state for {sid}. Name the recording with --file")
    here = str(genie_paths.checkout())
    best = None
    for p in folder.glob("*.json") if folder.is_dir() else []:
        st = read_json(p, {})
        if st.get("root") == here and (best is None or st.get("seen", 0) > best.get("seen", 0)):
            best = st
    return (best, "") if best else (None, "no session of this project has run the hooks. Use --file or --session")


def entry_text(row):
    parts = []
    for blk in blocks(row):
        kind = blk.get("type")
        if kind == "text":
            parts.append(blk.get("text", ""))
        elif kind == "tool_use":
            inp = blk.get("input") if isinstance(blk.get("input"), dict) else {}
            body = inp.get("command") if blk.get("name") in ("Bash", "PowerShell") else None
            parts.append(f"CALL {blk.get('name')}\n" + (body or json.dumps(inp, indent=1)))
        elif kind == "tool_result":
            parts.append(result_text(blk.get("content")))
    return "\n".join(p for p in parts if p)


def cmd_at(path, args):
    try:
        with open(path, "rb") as fh:
            fh.seek(args.at)
            row = json.loads(fh.readline())
    except Exception:
        return die(f"no entry starts at {args.at}. Take the @number from a search")
    text = entry_text(row)
    part = text[args.skip: args.skip + args.chars]
    out(f"@{args.at} {row.get('timestamp', '')} {row.get('type', '')}"
        + (" (subagent)" if row.get("isSidechain") else ""))
    out(part)
    more = len(text) - args.skip - len(part)
    if more > 0:
        out(f"\n{more} more characters. Next page: `genie recall --at {args.at} --skip {args.skip + len(part)}`")
    return 0


def cmd_gap(path, st):
    ck = st.get("ckpt") or {}
    found = gap(path, call=ck.get("call", ""), ckpt_time=ck.get("time", 0),
                hint=ck.get("offset", 0) or st.get("compact_offset", 0), root=st.get("root", ""))
    if ck:
        out("Work after the checkpoint of %s (%s):" % (
            time.strftime("%H:%M", time.localtime(ck.get("time", 0))), ck.get("topic", "")))
    else:
        out("This session has no checkpoint. Work since the last compaction:")
    if found["cut"]:
        out("The recording is large, so the oldest part of this stretch is not listed.")
    for it in found["items"]:
        out(one_line(it, 4_000))
    out(f"{len(found['items'])} lines. Full text of one: `genie recall --at <number>`")
    return 0


def cmd_search(path, args):
    words = [w.lower() for w in args.words]
    plain = [w.encode() for w in words if re.fullmatch(r"[a-z0-9 ._/-]+", w)]
    calls, hits = {}, []
    try:
        fh = open(path, "rb")
    except OSError as exc:
        return die(str(exc))
    with fh:
        pos = 0
        for raw in fh:
            at, pos = pos, pos + len(raw)
            if b'"tool_use"' not in raw and not all(w in raw.lower() for w in plain):
                continue
            try:
                row = json.loads(raw)
            except Exception:
                continue
            if not main_chain(row, args.all):
                continue
            for blk in blocks(row):
                if blk.get("type") == "tool_use":
                    calls[blk.get("id")] = f"{blk.get('name')}: {clip(call_head(blk.get('name'), blk.get('input')), 60)}"
            if row.get("type") == "user" and not message_text(row) and not any(
                    b.get("type") == "tool_result" for b in blocks(row)):
                continue
            text = entry_text(row)
            low = text.lower()
            if not all(w in low for w in words):
                continue
            kind = "said" if row.get("type") == "assistant" else "message"
            for blk in blocks(row):
                if blk.get("type") == "tool_use":
                    kind = "call"
                elif blk.get("type") == "tool_result":
                    kind = "result of " + calls.get(blk.get("tool_use_id"), "a call")
            i = low.find(words[0])
            hits.append(f"@{at} {kind}: {squash(text[max(0, i - 100): i + 220])}")
    if not hits:
        out("nothing found for: " + " ".join(args.words))
        return 0
    for line in list(reversed(hits))[: args.n]:
        out(line)
    shown = min(len(hits), args.n)
    out(f"{len(hits)} found" + (f", the newest {shown} shown. More with --n" if shown < len(hits) else "")
        + ". Full text of one: `genie recall --at <number>`")
    return 0


def main(argv=None):
    if not genie_paths.enabled():
        out("This project has not opted in. Run `genie init`.")
        return 1
    ap = argparse.ArgumentParser(prog="genie recall", description=__doc__.strip().splitlines()[0])
    ap.add_argument("words", nargs="*")
    ap.add_argument("--at", type=int)
    ap.add_argument("--gap", action="store_true")
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--chars", type=int, default=4_000)
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--session")
    ap.add_argument("--file")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if not (args.words or args.gap or args.at is not None):
        out(__doc__.strip())
        return 2
    st = {}
    if args.file:
        path = Path(args.file)
    else:
        st, err = session_state(args)
        if err:
            return die(err)
        path = Path(st.get("transcript") or "")
    if not path.is_file():
        return die(f"the recording {path} is gone. Claude Code deletes recordings after "
                   "`cleanupPeriodDays`, 30 days by default")
    if args.at is not None:
        return cmd_at(path, args)
    if args.gap:
        return cmd_gap(path, st)
    return cmd_search(path, args)


if __name__ == "__main__":
    sys.exit(main())
