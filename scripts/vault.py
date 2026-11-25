#!/usr/bin/env python3
"""Shared notes. The fleet's memory, readable by people and agents.

    genie vault index [--all]   the session-start digest
    genie vault check           lint notes: frontmatter, names, wikilinks, backlinks
    genie vault links <note>    what a note links to, and every line that links to it
    genie vault links           every note, the most linked first

`check` exits 1 when it finds an error. Warnings do not fail it. Plain
markdown with [[wikilinks]], so the folder also opens as an Obsidian vault.

`links` takes a note name (`api-batch-size`) or a path (`tracks/api/STATE`).
It reads the files, so it needs no Obsidian. Every note links back to the hub,
and that one link is left out because it says nothing.
"""
import re
import subprocess
import sys
from collections import Counter
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
LINKS_SHOWN = 40        # lines per list in `links`, lifted by --all
LINES_PER_NOTE = 5      # lines one note may add to "Linked from", newest kept
LINE_CHARS = 200


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


def without_code(text):
    """Blank out code blocks and code spans. Line numbers stay the same."""
    text = re.sub(r"```.*?```", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def finding_line(stem, fm):
    return (f"[{fm.get('status', '?')}] {stem} ({fm.get('track', '') or 'general'}): "
            f"{fm.get('summary', '')[:SUMMARY_CHARS]}")


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
    tail = ["", "Check findings before starting. `genie vault links <note>` shows what a note cites and what "
                "cites it. File findings, decisions and scores with the genie:checkpoint skill."]
    found = []
    for p in (notes_dir() / "findings").glob("*.md"):
        fm = frontmatter(read(p))
        found.append((fm.get("updated", ""), p.stem, "  " + finding_line(p.stem, fm)))
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
        for m in LINK_RE.finditer(without_code(text)):
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


# ---------------------------------------------------------------- links

def rel(p):
    return p.relative_to(top()).as_posix()


def note_index(files):
    """Lookups for a link target or a typed name, both without regard to case."""
    by_path, by_name = {}, {}
    for p in files:
        by_path[rel(p)[:-3].lower()] = p
        by_name.setdefault(p.stem.lower(), []).append(p)
    return by_path, by_name


def locate(name, index, near=None):
    """The notes a name can mean. More than one when the name is not unique.

    A name with a slash matches the end of a path, so `api/STATE` finds
    `tracks/api/STATE.md`. `near` is the note a link sits in. A link to a
    name that several notes share means the one in the same folder.
    """
    by_path, by_name = index
    t = name.replace("\\", "").split("|")[0].split("#")[0].strip().strip("/").lower()
    t = t[:-3] if t.endswith(".md") else t
    if "/" in t:
        return [p for path, p in by_path.items() if path == t or path.endswith("/" + t)]
    hits = by_name.get(t, [])
    if near is not None and len(hits) > 1:
        return [p for p in hits if p.parent == near.parent] or hits
    return hits


def note_links(files, index, hub):
    """Every link between two notes as (from, line number, line, to)."""
    found = []
    for p in files:
        text = read(p)
        for n, (line, bare) in enumerate(zip(text.splitlines(), without_code(text).splitlines()), 1):
            for m in LINK_RE.finditer(bare):
                hits = locate(m.group(1), index, near=p)
                if hits and hits[0] not in (p, hub):
                    found.append((p, n, re.sub(r"\s+", " ", line).strip(), hits[0]))
    return found


def about(p, path=True):
    """One line on a note. A finding shows its status, as it does in the digest."""
    fm = frontmatter(read(p))
    if fm.get("type") == "finding":
        return finding_line(p.stem, fm)
    text = fm.get("summary", "")[:SUMMARY_CHARS] or fm.get("type") or "no summary"
    return f"{rel(p)}: {text}" if path else text


def count(n, what="note"):
    return f"{n} {what}" + ("" if n == 1 else "s")


def clip(lines, limit, what):
    if not limit or len(lines) <= limit:
        return lines
    return lines[:limit] + [f"  ... {len(lines) - limit} more {what}. `--all` lists them."]


def cmd_links(argv):
    everything = "--all" in argv
    limit = 0 if everything else LINKS_SHOWN
    names = [a for a in argv if not a.startswith("--")]
    files = list(md_files())
    index = note_index(files)
    hub = genie_paths.hub_note(top())
    links = note_links(files, index, hub)
    if not names:
        pairs = {(src, dst) for src, _, _, dst in links}
        n_in, n_out = Counter(dst for _, dst in pairs), Counter(src for src, _ in pairs)
        rows = sorted(files, key=lambda p: (-n_in[p], -n_out[p], rel(p)))
        out("Notes by how many notes link to them. The link back to the hub is not counted.")
        out("   in  out")
        for line in clip([f"  {n_in[p]:>3}  {n_out[p]:>3}  {about(p)}" for p in rows], limit, "notes"):
            out(line)
        out("`genie vault links <note>` shows the lines behind a count.")
        return 0
    typed = Path(names[0])
    here = typed if typed.is_absolute() else Path.cwd() / typed
    hits = [p for p in files if here.is_file() and p.resolve() == here.resolve()] or locate(names[0], index)
    if len(hits) != 1:
        key = Path(names[0]).stem.lower()
        close = hits or [p for p in files if key and key in p.stem.lower()]
        out(f"{len(hits)} notes are named `{names[0]}`." if hits else f"No note is named `{names[0]}`.")
        for p in close[:12]:
            out("  " + rel(p))
        out("Pass one of these paths." if close else "`genie vault links` lists every note.")
        return 1
    note = hits[0]
    out(rel(note))
    out(about(note, path=False))
    to = list(dict.fromkeys(dst for src, _, _, dst in links if src == note))
    out("")
    out(f"Links to {count(len(to))}" + (":" if to else "."))
    for line in clip(["  " + about(p) for p in to], limit, "notes"):
        out(line)
    by_note = {}
    for src, n, line, dst in links:
        if dst == note:
            by_note.setdefault(src, []).append(f"  {rel(src)}:{n}  {line[:LINE_CHARS]}")
    shown = []
    for src, lines in by_note.items():
        if not everything and len(lines) > LINES_PER_NOTE:
            shown.append(f"  {rel(src)}: {count(len(lines) - LINES_PER_NOTE, 'earlier line')} left out")
            lines = lines[-LINES_PER_NOTE:]
        shown += lines
    out("")
    if note == hub:
        out("Every note links back to the hub. Those links are left out.")
    else:
        out(f"Linked from {count(len(by_note))}" + (":" if by_note else "."))
        for line in clip(shown, limit, "lines"):
            out(line)
    return 0


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
    if cmd == "links":
        return cmd_links(argv[1:])
    out(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
