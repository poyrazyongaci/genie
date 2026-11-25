#!/usr/bin/env python3
"""One-call checkpoint: save a track's state AND file what was learned.

    genie checkpoint --track <slug> "<topic>" < entry
    genie checkpoint --track none "<topic>" < entry     cross-track work, no STATE.md
    add --dry-run to validate without writing

Stdin is the state body (## Goal / ## Done / ## Next / ## Blocked), then a line
`===DURABLE===`, then one or more directives:

    >>> finding <kebab-slug> | <one-line summary> | status: draft|confirmed|dead
    <body: ## What we tried / ## What happened / ## So what>
    >>> append notes/findings/<slug>.md#<Heading>
    - lines to append
    >>> decision <what we decided> | <why> | <who>
    >>> board <track name> | local: <x> | outside: <y> | next: <idea> | owner: <name>
    >>> score <local> | <outside> | <commit> | <note>
    >>> none: <why nothing durable came out of this stretch>

It validates every directive before writing anything, then writes, runs the
notes check and prints the exact git commands for only the files it touched.
It never commits or pushes.

Every checkpoint also adds one row to a timeline that is never trimmed:
tracks/<slug>/TIMELINE.md, or notes/timeline.md for cross-track work. STATE.md
says where a track is. The timeline says how it got there.

A checkpoint that was saved leaves a receipt in ~/.claude/genie/receipts and
prints `receipt: <id>`. The keep-alive hooks count a checkpoint only when they
find that receipt. A command that merely mentions a checkpoint leaves none.
"""
import datetime as dt
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genie_paths  # noqa: E402
import vault  # noqa: E402

ROOT = genie_paths.checkout()
_CFG = genie_paths.config()
NOTES = ROOT / _CFG["notes"]
HUB = _CFG["hub"]
OVERVIEW = NOTES / f"{HUB}.md"
DECISIONS = NOTES / "decisions.md"
DURABLE = "===DURABLE==="
MARK = "<!-- checkpoint -->"
KEEP = 3
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SCORES_HEAD = "| Time | Commit | Local score | Outside score | Note |\n|---|---|---|---|---|\n"
TIMELINE_HEAD = "| Time | Who | Topic | Next | Filed |\n|---|---|---|---|---|\n"
RECEIPT_DAYS = 14   # an unclaimed receipt is of no use to anyone after this
BOARD_COLS = {"owner": 1, "local": 2, "outside": 3, "board": 3, "next": 4}


def die(msg: str):
    sys.stderr.write("refusing: " + msg + "\n")
    sys.exit(1)


def say(msg: str = ""):
    sys.stdout.buffer.write((msg + "\n").encode("utf-8"))


def git(*args, timeout=8) -> str:
    try:
        r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")


def bump_updated(text: str, today: str) -> str:
    if re.search(r"^updated:.*$", text, re.M):
        return re.sub(r"^updated:.*$", "updated: " + today, text, count=1, flags=re.M)
    return text


def append_under(text: str, heading: str, block: str) -> str:
    if not heading:
        return text.rstrip("\n") + "\n\n" + block + "\n"
    m = re.search(r"^(#{1,6})\s+" + re.escape(heading) + r"\s*$", text, re.M)
    if not m:
        return text.rstrip("\n") + "\n\n## " + heading + "\n\n" + block + "\n"
    nxt = re.compile(r"^#{1,%d}\s" % len(m.group(1)), re.M).search(text, m.end())
    cut = nxt.start() if nxt else len(text)
    tail = text[cut:]
    return text[:cut].rstrip("\n") + "\n" + block + "\n" + ("\n" + tail if tail else "")


def board_rows(text: str, name: str):
    """Line numbers of data rows in the `## Status board` table whose track cell contains name."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"^##\s+Status board\s*$", ln)), None)
    if start is None:
        die("overview has no `## Status board` section")
    hits = []
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("#"):
            break
        if lines[i].startswith("|") and not re.match(r"^\|[\s|:-]+\|$", lines[i]):
            track_cell = re.split(r"(?<!\\)\|", lines[i])[1]
            if name.lower() in track_cell.lower():
                hits.append(i)
    return hits


def parse(raw: str):
    if DURABLE not in raw:
        die("no %s section. Answer the durable gate, at least `>>> none: <reason>`." % DURABLE)
    body, rest = raw.split(DURABLE, 1)
    items, cur = [], None
    for line in rest.splitlines():
        if line.startswith(">>>"):
            cur = {"head": line[3:].strip(), "lines": []}
            items.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
        elif line.strip():
            die("text after %s must start with a `>>>` directive" % DURABLE)
    if not items:
        die("durable section is empty. Use `>>> none: <reason>` if nothing qualifies.")
    return body.strip(), items


def fields(head: str):
    parts = [p.strip() for p in head.split("|")]
    kv = {}
    for p in parts[1:]:
        k, sep, v = p.partition(":")
        if sep and k.strip().lower() in {"status", "local", "board", "outside", "next", "owner", "track"}:
            kv[k.strip().lower()] = v.strip()
    return parts, kv


def finding_note(slug, summary, status, track, body, today):
    tags = ["finding"] + ([track] if track else [])
    fm = ["---", "date: " + today, "updated: " + today, "tags: [" + ", ".join(tags) + "]",
          "type: finding", "status: " + status, "track: " + (track or ""), "summary: " + summary, "---", ""]
    title = slug.replace("-", " ").capitalize()
    text = "\n".join(fm) + "\n# " + title + "\n\nBack to [[" + HUB + "]].\n\n" + body.strip() + "\n"
    return text


def cell(text, cap=160):
    text = re.sub(r"\s+", " ", str(text)).replace("|", "/").strip()
    return text if len(text) <= cap else text[: cap - 3] + "..."


def timeline_row(track, today, now, who, topic, body, filed):
    """Append one row for this checkpoint. Returns the file it went to."""
    if track:
        p = ROOT / "tracks" / track / "TIMELINE.md"
        title, what = "%s timeline" % track, "the %s track" % track
    else:
        p = NOTES / "timeline.md"
        title, what = "Timeline", "cross-track work"
    if p.exists():
        text = bump_updated(read(p), today)
    else:
        text = ("---\ndate: %s\nupdated: %s\ntags: [timeline%s]\ntype: log\n"
                "summary: Every checkpoint of %s, one row each, oldest first. Never trimmed.\n---\n\n"
                "# %s\n\nBack to [[%s]].\n"
                "One row per `genie checkpoint`. Append only. The newest state is in %s.\n\n%s" % (
                    today, today, ", " + track if track else "", what, title, HUB,
                    "`STATE.md`" if track else "each track's `STATE.md`", TIMELINE_HEAD))
    m = re.search(r"^## Next\s*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    nxt = next((ln.strip().lstrip("-* ").strip() for ln in (m.group(1) if m else "").splitlines() if ln.strip()), "")
    row = "| %s | %s | %s | %s | %s |" % (now.strftime("%Y-%m-%d %H:%M %Z"), cell(who, 40), cell(topic, 90),
                                          cell(nxt), cell(", ".join(filed), 400))
    write(p, text.rstrip("\n") + "\n" + row)
    return p


def issue_receipt(now, track, topic, who, entry):
    """Proof for the keep-alive hooks that this checkpoint was saved. It is
    bound to the session that ran it and holds that session's own state entry,
    so a second session on the track can never be mistaken for this one."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    rid = "%d-%s" % (int(now.timestamp() * 1000), secrets.token_hex(4))
    try:
        genie_paths.write_json(genie_paths.RECEIPTS / (genie_paths.session_key(sid) or "_") / (rid + ".json"), {
            "id": rid, "time": now.timestamp(), "session": sid, "root": str(ROOT), "track": track,
            "topic": topic, "who": who, "entry": entry})
    except OSError as exc:
        return "failed (%s)" % exc
    limit = now.timestamp() - RECEIPT_DAYS * 86400
    try:
        for old in list(genie_paths.RECEIPTS.glob("*/*.json"))[:2000]:
            if old.stat().st_mtime < limit:
                old.unlink()
    except OSError:
        pass
    return rid


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not genie_paths.enabled():
        die("this project has not opted in. Run `genie init`")
    dry = "--dry-run" in args
    if dry:
        args.remove("--dry-run")
    track = ""
    if "--track" in args:
        i = args.index("--track")
        track = args[i + 1].strip()
        del args[i:i + 2]
    if len(args) != 1 or not track:
        say(__doc__)
        sys.exit(2)
    topic = args[0].strip()
    if track == "none":
        track = ""
    elif not SLUG_RE.match(track) or not (ROOT / "tracks" / track).is_dir():
        die("tracks/%s does not exist. `genie track add %s` makes it" % (track, track))

    body, items = parse(sys.stdin.read())
    now = dt.datetime.now().astimezone()
    today = now.strftime("%Y-%m-%d")
    who = git("config", "user.name") or "unknown"
    plan, touched = [], []

    # validate everything first
    for it in items:
        head, content = it["head"], "\n".join(it["lines"]).strip()
        kind = head.split(None, 1)[0].lower().rstrip(":")
        rest = head[len(head.split(None, 1)[0]):].strip()
        if kind == "none":
            plan.append(("none", rest.lstrip(":").strip()))
        elif kind == "finding":
            parts, kv = fields(rest)
            slug = parts[0]
            if not SLUG_RE.match(slug):
                die("finding slug `%s` must be lowercase kebab-case" % slug)
            if len(parts) < 2 or not parts[1] or ":" in parts[1].split()[0]:
                die("finding needs a summary: >>> finding <slug> | <summary> | status: draft")
            status = kv.get("status", "draft")
            if status not in {"draft", "confirmed", "dead"}:
                die("status must be draft, confirmed or dead")
            if not content:
                die("finding %s has no body" % slug)
            plan.append(("finding", slug, parts[1], status, kv.get("track", track), content))
        elif kind == "append":
            rel, _, heading = rest.partition("#")
            rel = rel.strip().replace("\\", "/")
            p = (ROOT / rel).resolve()
            if ROOT.resolve() not in p.parents or not p.exists() or p.suffix != ".md":
                die("append target %s must be an existing .md in the repo" % rel)
            if p in (OVERVIEW, DECISIONS):
                die("use `board` or `decision` for %s" % rel)
            if not content:
                die("append to %s has no content" % rel)
            plan.append(("append", p, heading.strip(), content))
        elif kind == "decision":
            parts = [x.strip() for x in rest.split("|")]
            if len(parts) < 2 or not all(parts[:2]):
                die("decision needs: >>> decision <what> | <why> | <who>")
            plan.append(("decision", parts[0], parts[1], parts[2] if len(parts) > 2 and parts[2] else who))
        elif kind == "board":
            parts, kv = fields(rest)
            if not parts[0] or not kv:
                die("board needs: >>> board <track name> | local: x | outside: y | next: z")
            plan.append(("board", parts[0], kv))
        elif kind == "score":
            if not track:
                die("score needs --track <slug>")
            parts = [x.strip() for x in rest.split("|")]
            if len(parts) < 3:
                die("score needs: >>> score <local> | <outside> | <commit> | <note>")
            plan.append(("score", parts + [""] * (4 - len(parts))))
        else:
            die("unknown directive `%s`" % kind)

    # board rows must exist before writing anything
    if not OVERVIEW.exists() or not DECISIONS.exists():
        die("%s or decisions.md is missing. Run `genie init`" % OVERVIEW.name)
    ov = read(OVERVIEW)
    for step in plan:
        if step[0] == "board" and len(board_rows(ov, step[1])) != 1:
            die("board track `%s` matches %d status board rows, need exactly 1" % (
                step[1], len(board_rows(ov, step[1]))))

    if dry:
        for step in plan:
            say("DRY " + " ".join(str(x)[:60] for x in step))
        return

    behind = git("rev-list", "--count", "HEAD..@{u}") if git("fetch", "-q", timeout=10) == "" else ""
    if behind and behind != "0":
        say("WARNING: %s commits behind origin. Run `git pull --rebase` before committing these notes." % behind)

    report, filed, entry = [], [], ""
    for step in plan:
        kind = step[0]
        if kind == "none":
            report.append("durable: none (%s)" % step[1])
        elif kind == "finding":
            _, slug, summary, status, trk, content = step
            p = NOTES / "findings" / (slug + ".md")
            if p.exists():
                text = bump_updated(read(p), today)
                if status != "draft":
                    text = re.sub(r"^status:.*$", "status: " + status, text, count=1, flags=re.M)
                text = append_under(text, "Updates", "### %s (%s)\n\n%s" % (today, who, content))
                write(p, text)
                report.append("finding: appended to [[%s]] (already existed), status %s" % (slug, status))
            else:
                write(p, finding_note(slug, summary, status, trk, content, today))
                ov = append_under(ov, "Findings", "- [[%s]] %s" % (slug, summary[:90]))
                report.append("finding: new [[%s]] and listed in the overview" % slug)
                touched.append(OVERVIEW)
            touched.append(p)
            filed.append("[[%s]]" % slug)
        elif kind == "append":
            _, p, heading, content = step
            write(p, append_under(bump_updated(read(p), today), heading, content))
            touched.append(p)
            report.append("append: %s%s" % (p.relative_to(ROOT).as_posix(), "#" + heading if heading else ""))
            filed.append("[[%s]]" % p.stem if NOTES in p.parents else p.relative_to(ROOT).as_posix())
        elif kind == "decision":
            _, what, why, by = step
            row = "| %s | %s | %s | %s |" % (today, what.replace("|", "/"), why.replace("|", "/"), by)
            write(DECISIONS, bump_updated(read(DECISIONS), today).rstrip("\n") + "\n" + row)
            touched.append(DECISIONS)
            report.append("decision: %s" % what[:70])
            filed.append("decision: %s" % what[:60])
        elif kind == "board":
            _, name, kv = step
            lines = ov.splitlines()
            i = board_rows(ov, name)[0]
            cells = re.split(r"(?<!\\)\|", lines[i])[1:-1]
            for k, v in kv.items():
                if k in BOARD_COLS and BOARD_COLS[k] < len(cells):
                    cells[BOARD_COLS[k]] = " " + v.replace("|", "/") + " "
            lines[i] = "|" + "|".join(cells) + "|"
            ov = "\n".join(lines)
            touched.append(OVERVIEW)
            report.append("board: %s -> %s" % (name, ", ".join("%s=%s" % kv_ for kv_ in kv.items())))
        elif kind == "score":
            local, board, commit, note = step[1][:4]
            p = ROOT / "tracks" / track / "SCORES.md"
            text = read(p) if p.exists() else "# %s submissions\n\n%s" % (track, SCORES_HEAD)
            row = "| %s | %s | %s | %s | %s |" % (now.strftime("%Y-%m-%d %H:%M"), commit, local, board, note)
            write(p, text.rstrip("\n") + "\n" + row)
            touched.append(p)
            report.append("score: %s local %s outside %s" % (track, local, board))
            filed.append("score %s" % local)

    if OVERVIEW in touched:
        write(OVERVIEW, bump_updated(ov, today))

    # track state ring buffer, newest first
    if track and body:
        p = ROOT / "tracks" / track / "STATE.md"
        old = read(p) if p.exists() else ""
        entries = [e.strip() for e in old.split(MARK)[1:] if e.strip()]
        entry = "## Checkpoint %s %s %s (%s, %s)\n\n%s" % (today, now.strftime("%H:%M"), now.tzname(), who, topic, body)
        entries = ([entry] + entries)[:KEEP]
        fm = ("---\ndate: %s\nupdated: %s\nowner: %s\ntags: [state, %s]\ntype: state\n"
              "summary: Latest working state of the %s track. Newest checkpoint first.\n---\n\n"
              "# %s track state\n\nBack to [[%s]].\n"
              "Rewritten by `genie checkpoint`. Keeps the last %d checkpoints.\n\n" % (
                  today, today, who, track, track, track, HUB, KEEP))
        write(p, fm + "\n\n".join(MARK + "\n" + e for e in entries))
        touched.append(p)
        report.append("state: tracks/%s/STATE.md (%d kept)" % (track, len(entries)))

    p = timeline_row(track, today, now, who, topic, body, filed)
    touched.append(p)
    report.append("timeline: %s" % p.relative_to(ROOT).as_posix())
    report.append("receipt: %s" % issue_receipt(now, track, topic, who, entry))

    for r in report:
        say(r)
    chk = subprocess.run([sys.executable, str(Path(vault.__file__)), "check"], cwd=str(ROOT),
                         capture_output=True, text=True, encoding="utf-8")
    say((chk.stdout or "").strip().splitlines()[-1] if chk.stdout else "vault check did not run")
    if chk.returncode:
        say(chk.stdout.strip())
    rels = sorted({p.relative_to(ROOT).as_posix() for p in touched})
    if rels:
        quoted = " ".join('"%s"' % r for r in rels)
        say("\nCommit only these files (check `git diff` on shared notes first):")
        say("git add -- %s" % quoted)
        say('git commit -m "notes: %s" -- %s' % (topic.replace('"', "'"), quoted))


if __name__ == "__main__":
    sys.exit(main())
