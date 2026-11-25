#!/usr/bin/env python3
"""Shared notes. The fleet's memory, readable by people and agents.

    genie vault index [--all]   the session-start digest
    genie vault check           lint notes: frontmatter, names, wikilinks, backlinks

`check` exits 1 when it finds an error. Warnings do not fail it. Plain
markdown with [[wikilinks]], so the folder also opens as an Obsidian vault.
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genie_paths  # noqa: E402

SKIP_DIRS = {".git", ".obsidian", ".venv", "kit", "templates", "attachments", "node_modules",
             "target", "lab", "genie-lab", "briefs", "logs"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.md$")
LINK_RE = re.compile(r"!?\[\[([^\]]+)\]\]")
FINDING_STATUS = {"draft", "confirmed", "dead"}
DECISIONS_SHOWN = 5
SUMMARY_CHARS = 140


def out(text=""):
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def top():
    return genie_paths.checkout()


def notes_dir():
    return top() / genie_paths.config()["notes"]


def md_files():
    for base in (notes_dir(), top() / "tracks"):
        for p in sorted(base.rglob("*.md")) if base.is_dir() else []:
            if not SKIP_DIRS.intersection(p.relative_to(top()).parts[:-1]):
                yield p


def read(p):
    return p.read_text(encoding="utf-8", errors="replace")


def frontmatter(text):
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def section(text, heading):
    m = re.search(r"^##\s+" + re.escape(heading) + r"\s*$", text, re.M)
    if not m:
        return ""
    nxt = re.search(r"^##\s", text[m.end():], re.M)
    return text[m.end(): m.end() + nxt.start()] if nxt else text[m.end():]


def table_rows(block):
    rows = [ln for ln in block.splitlines() if ln.strip().startswith("|")]
    return [r for r in rows if not re.match(r"^\|[\s|:-]+\|$", r.strip())]


def git(*args):
    try:
        r = subprocess.run(["git", "-C", str(top()), *args], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


# ---------------------------------------------------------------- index

def plain(row):
    return re.sub(r"\[\[[^\]|]*\\\|([^\]]+)\]\]", r"\1", row)


def digest(limit=0):
    """Session-start digest. `limit` caps its size in characters, 0 means all.

    The digest is paid for at every session start and after every compaction,
    so the status board, decisions and track state come first and the list of
    findings fills what is left, newest first.
    """
    cfg = genie_paths.config()
    hub = genie_paths.hub_note(top())
    if not hub.exists():
        return ""
    head = [f"SHARED NOTES. Hub: {cfg['notes']}/{cfg['hub']}.md"]
    behind = git("rev-list", "--count", "HEAD..@{u}")
    if behind and behind != "0":
        head.append(f"SYNC: {behind} commits behind upstream as of the last fetch. Pull before editing shared notes.")
    dirty = git("status", "--porcelain", "--", cfg["notes"], "tracks")
    if dirty:
        head.append(f"SYNC: {len(dirty.splitlines())} uncommitted note or track files. Someone's work is in progress here.")
    board = table_rows(section(read(hub), "Status board"))
    if len(board) > 1:
        head += ["", "Status board:"] + ["  " + plain(r) for r in board]
    dec = notes_dir() / "decisions.md"
    rows = table_rows(read(dec))[1:] if dec.exists() else []
    if rows:
        head += ["", f"Latest decisions ({len(rows)} total, newest last):"] + \
                ["  " + plain(r)[:400] for r in rows[-DECISIONS_SHOWN:]]
    states = sorted((top() / "tracks").glob("*/STATE.md"))
    if states:
        head += ["", "Track state (latest checkpoint per track):"]
        for p in states:
            text = read(p)
            fm = frontmatter(text)
            nxt = section(text, "Next").strip().splitlines()
            head.append(f"  {p.parent.name}, updated {fm.get('updated', '?')} by {fm.get('owner', '?')}. "
                        f"Next: {(nxt[0] if nxt else '?')[:SUMMARY_CHARS]}")
    tail = ["", "Check findings before starting. File findings, decisions and scores with the genie:checkpoint skill."]
    found = []
    for p in (notes_dir() / "findings").glob("*.md"):
        fm = frontmatter(read(p))
        found.append((fm.get("updated", ""), p.stem,
                      f"  [{fm.get('status', '?')}] {p.stem} ({fm.get('track', '') or 'general'}): "
                      f"{fm.get('summary', '')[:SUMMARY_CHARS]}"))
    found.sort(reverse=True)
    lines = [f[2] for f in found]
    used = len("\n".join(head + tail)) + 120
    shown = []
    for line in lines:
        if limit and used + len(line) + 1 > limit:
            break
        shown.append(line)
        used += len(line) + 1
    if lines:
        more = len(lines) - len(shown)
        head += ["", f"Findings ({len(lines)}). Grep or read one before redoing its work:"] + shown
        if more:
            head.append(f"  ... {more} older ones. `genie vault index --all` lists them.")
    text = "\n".join(head + tail)
    return text if not limit or len(text) <= limit else text[:limit - 40] + "\n... cut. `genie vault index --all`"


# ---------------------------------------------------------------- check

def resolve(target, names, paths):
    target = target.replace("\\", "").split("|")[0].split("#")[0].strip()
    if not target:
        return True  # same-note heading link
    if "/" in target:
        return (target if "." in Path(target).name else target + ".md") in paths
    if "." in target and not target.endswith(".md"):
        return target in names  # attachment embed
    return (target[:-3] if target.endswith(".md") else target) in names


def cmd_check():
    errors, warns = [], []
    cfg = genie_paths.config()
    hub, files = genie_paths.hub_note(top()), list(md_files())
    every = [p for base in (notes_dir(), top() / "tracks") if base.is_dir() for p in base.rglob("*") if p.is_file()]
    names = {p.stem for p in files} | {p.name for p in every}
    paths = {p.relative_to(top()).as_posix() for p in every}
    for p in files:
        rel = p.relative_to(top()).as_posix()
        text = read(p)
        fm = frontmatter(text)
        is_note = rel.startswith(cfg["notes"] + "/")
        is_finding = rel.startswith(cfg["notes"] + "/findings/")
        is_state = p.name == "STATE.md"
        code_free = re.sub(r"`[^`\n]*`", "", re.sub(r"```.*?```", "", text, flags=re.S))
        for m in LINK_RE.finditer(code_free):
            if not resolve(m.group(1), names, paths):
                errors.append(f"{rel}: broken link [[{m.group(1)}]]")
        if not (is_note or is_state):
            continue
        if is_note and not NAME_RE.match(p.name):
            errors.append(f"{rel}: filename must be lowercase kebab-case")
        if not fm:
            errors.append(f"{rel}: missing YAML frontmatter")
            continue
        for key in ["date", "tags", "type"] + (["updated", "status", "summary"] if is_finding else []):
            if not fm.get(key):
                errors.append(f"{rel}: frontmatter `{key}` is empty or missing")
        if is_finding and fm.get("status") and fm["status"] not in FINDING_STATUS:
            errors.append(f"{rel}: status `{fm['status']}` is not one of {'/'.join(sorted(FINDING_STATUS))}")
        if is_finding and len(fm.get("summary", "")) > 220:
            warns.append(f"{rel}: summary is long, the digest cuts it at {SUMMARY_CHARS} characters")
        if p != hub and f"[[{cfg['hub']}" not in text:
            errors.append(f"{rel}: no backlink [[{cfg['hub']}]]")
    for e in errors:
        out("ERROR " + e)
    for w in warns:
        out("warn  " + w)
    out(f"vault check: {len(files)} files, {len(errors)} errors, {len(warns)} warnings")
    return 1 if errors else 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "index"
    if not genie_paths.enabled():
        out("This project has not opted in. Run `genie init`.")
        return 1
    if cmd == "index":
        out(digest(0 if "--all" in argv else int(genie_paths.config().get("digest_chars") or 6000)))
        return 0
    if cmd == "check":
        return cmd_check()
    out(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
