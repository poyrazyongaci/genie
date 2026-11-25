#!/usr/bin/env python3
"""Genie lab. The bookkeeping behind `genie wish`.

A lab is one folder, `tracks/<slug>/lab/`. Every fact about a wish lives in
its files, so a fresh session, a compacted session or a teammate can pick
the work up from disk.

    WISH.md      problem card
    genie.toml   score commands per tier, scorer paths, width. People edit it
    BACKLOG.md   ideas with rank, source, first test, files, status
    ledger.tsv   one row per scored attempt, append only
    GATES.md     waiting on the human
    state.json   facts the script measured: noise, scorer hash, clocks
    briefs/      one file per worker task
    golden/      entries with a known score
    logs/        one log per score run

The stage is never stored. `genie next` works it out from these files as
the first stage whose exit check fails.

This module has no judgment in it. It counts, times, hashes and compares.
The stage checks exist so that a score can be trusted before anything is
built on it.
"""
import fnmatch
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python older than 3.11
    tomllib = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genie_paths  # noqa: E402
from genie_paths import read_json, root, write_json  # noqa: E402

TIERS = ("T0", "T1", "T2", "T3")
COLS = ["id", "time", "worker", "idea", "base", "tier", "score", "runs", "spread",
        "t_edit", "t_build", "t_score", "t_wait", "verdict", "evidence", "ref", "note"]
VERDICTS = ("base", "win", "loss", "keep", "reject", "invalid", "superseded", "info")
PHASES = ("t_edit", "t_build", "t_score", "t_wait")
STAGES = ("Preflight", "Plan", "Scorer", "Baseline", "Scout", "Iterate")
WISH_FIELDS = ("Problem", "Scoring rule", "Limits", "Done when", "Rules")
BACKLOG_HEAD = ["id", "rank", "idea", "source", "first test", "files", "status"]
GATES_HEAD = ["id", "opened", "kind", "scope", "what", "asked by", "status", "answer"]
GATE_KINDS = ("push", "submit", "secret", "spend", "other")
IDEA_STATUS = ("open", "running", "gated", "kept", "rejected")
ROSTER_MAX_AGE = 24 * 3600
STALL_REJECTS = 3
STALL_SECONDS = 3600
AGREE_MIN_PAIRS = 4
AGREE_FLOOR = 0.8
TUNE_SHARE = 0.5
TUNE_ROWS = 10
TUNE_MIN_ROWS = 5


class GenieError(Exception):
    """A refusal with a message for the agent. Nothing was written."""


# ---------------------------------------------------------------- paths

def lab_dir(track):
    if not track or not re.fullmatch(r"[A-Za-z0-9_-]+", track):
        raise GenieError("pass --track <slug>")
    # `genie-lab` wins when it exists. A track that already keeps its own
    # `lab/` folder on another branch uses it to stay clear of a merge.
    alt = root() / "tracks" / track / "genie-lab"
    return alt if (alt / "genie.toml").exists() else root() / "tracks" / track / "lab"


def need_lab(track):
    lab = lab_dir(track)
    if not (lab / "genie.toml").exists():
        raise GenieError(f"no lab for {track}. Run `genie wish` for a new "
                         "problem or `genie adopt` for a track already running")
    return lab


def labs():
    found = {p.parent.parent.name: p.parent for p in root().glob("tracks/*/lab/genie.toml")}
    found.update({p.parent.parent.name: p.parent for p in root().glob("tracks/*/genie-lab/genie.toml")})
    return [found[k] for k in sorted(found)]


def worker_name(args=None):
    return (getattr(args, "worker", None) or os.environ.get("GENIE_WORKER") or "conductor")


def now_iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t or time.time()))


def from_iso(s):
    try:
        return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def clean(text):
    return re.sub(r"\s+", " ", str(text if text is not None else "")).strip()


# ---------------------------------------------------------------- mutex and state

@contextmanager
def mutex(lab, wait=20.0):
    """One writer at a time for the ledger, state.json and the tables."""
    d = lab / ".locks"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "_write.mutex"
    end = time.time() + wait
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > 30:
                    path.unlink()  # a crashed writer left it behind
                    continue
            except OSError:
                continue
            if time.time() > end:
                raise GenieError("another genie command holds the lab. Try again")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def load_state(lab):
    return read_json(lab / "state.json", {})


def update_state(lab, fn):
    with mutex(lab):
        st = load_state(lab)
        fn(st)
        write_json(lab / "state.json", st)
    return st


# ---------------------------------------------------------------- config

def load_cfg(lab):
    if tomllib is None:
        raise GenieError("genie labs need Python 3.11 or newer (tomllib)")
    try:
        cfg = tomllib.loads((lab / "genie.toml").read_text(encoding="utf-8"))
    except OSError:
        raise GenieError(f"{lab / 'genie.toml'} is missing")
    except Exception as exc:
        raise GenieError(f"genie.toml does not parse: {exc}")
    if cfg.get("better") not in ("lower", "higher"):
        raise GenieError('genie.toml needs better = "lower" or better = "higher"')
    return cfg


def tier_cmd(cfg, tier):
    return clean(((cfg.get("tiers") or {}).get(tier) or {}).get("cmd", ""))


TOML_TEMPLATE = '''# Genie lab config. People and the conductor edit this file.
# Scripts only read it. Measured facts go to state.json.
track = "{track}"
better = "{better}"      # which way the score improves: lower or higher
width = 2                # isolated-write workers allowed at once
keep_runs = 3            # runs behind a keep at T2
timeout = 3600           # seconds, one score run
golden_tol = 0.0         # allowed gap on golden replay, on top of 2 times noise

# Write a command with a Windows path in single quotes: cmd = 'C:\\tools\\x.exe {{entry}}'
# {{entry}} is replaced by the entry path. Every tier measures the same
# thing as the board rule. A faster tier is a smaller or cached run of it.
[tiers.T0]
cmd = ""                 # smoke, seconds
[tiers.T1]
cmd = ""                 # proxy subset
[tiers.T2]
cmd = ""                 # full local score
[tiers.T3]
cmd = ""                 # board. Usually empty, results arrive through `genie log`

[build]
cmd = ""                 # optional, timed as t_build before each score

[parse]
regex = ""               # one capture group. Empty: last number on the last non-empty line

[scorer]
paths = []               # files and globs the scorer owns, relative to the repo root
[solution]
paths = []               # files and globs a solver may edit

[secrets]
files = []               # paths that must exist, never their content

[preflight]
tools = ["git"]
'''


# ---------------------------------------------------------------- markdown tables

def md_read(path):
    """(lines before the table, rows, lines after). Rows are lists of cells."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], [], []
    start = next((i for i, l in enumerate(lines) if l.lstrip().startswith("|")), None)
    if start is None:
        return lines, [], []
    end = start
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines[start + 2:end]]
    return lines[:start], rows, lines[end:]


def md_write(path, pre, head, rows, post):
    out = list(pre)
    out.append("| " + " | ".join(head) + " |")
    out.append("|" + "|".join("---" for _ in head) + "|")
    for r in rows:
        cells = [clean(c).replace("|", "/") for c in r] + [""] * (len(head) - len(r))
        out.append("| " + " | ".join(cells[:len(head)]) + " |")
    out.extend(post)
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def table(path, head):
    pre, rows, post = md_read(path)
    dicts = [dict(zip(head, r + [""] * (len(head) - len(r)))) for r in rows]
    return pre, dicts, post


def save_table(path, head, pre, dicts, post):
    md_write(path, pre, head, [[d.get(h, "") for h in head] for d in dicts], post)


def next_id(dicts, prefix):
    nums = [int(d["id"][len(prefix):]) for d in dicts
            if d.get("id", "").startswith(prefix) and d["id"][len(prefix):].isdigit()]
    return f"{prefix}{max(nums, default=0) + 1}"


# ---------------------------------------------------------------- ledger

def read_ledger(lab):
    try:
        lines = (lab / "ledger.tsv").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for l in lines[1:]:
        if not l.strip():
            continue
        cells = l.split("\t")
        rows.append(dict(zip(COLS, cells + [""] * (len(COLS) - len(cells)))))
    return rows


def append_row(lab, **fields):
    """Append one row. Returns it with its id. Never edits an old row."""
    bad = set(fields) - set(COLS)
    if bad:
        raise GenieError(f"unknown ledger columns: {sorted(bad)}")
    if fields.get("tier") not in TIERS:
        raise GenieError(f"tier must be one of {', '.join(TIERS)}")
    if fields.get("verdict") not in VERDICTS:
        raise GenieError(f"verdict must be one of {', '.join(VERDICTS)}")
    with mutex(lab):
        path = lab / "ledger.tsv"
        rows = read_ledger(lab)
        row = {c: clean(fields.get(c, "")) for c in COLS}
        row["id"] = str(max((int(r["id"]) for r in rows if r["id"].isdigit()), default=0) + 1)
        row["time"] = row["time"] or now_iso()
        new = not path.exists() or path.stat().st_size == 0
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            if new:
                fh.write("\t".join(COLS) + "\n")
            fh.write("\t".join(row[c] for c in COLS) + "\n")
    return row


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def live_rows(rows):
    """Rows that still count. A `superseded` row kills the row it points at."""
    dead = {r["ref"] for r in rows if r["verdict"] == "superseded" and r["ref"]}
    return [r for r in rows if r["id"] not in dead and r["verdict"] != "superseded"]


def better(cfg, a, b):
    """True when score a beats score b."""
    return a < b if cfg["better"] == "lower" else a > b


def gain(cfg, new, ref):
    return (ref - new) if cfg["better"] == "lower" else (new - ref)


def reference(rows, tier):
    """The score an attempt must beat at this tier: the newest base row, or
    the newest scored row of an idea that was kept."""
    live = live_rows(rows)
    kept = {r["idea"] for r in live if r["verdict"] == "keep" and r["idea"]}
    cands = [r for r in live if r["tier"] == tier and num(r["score"]) is not None
             and (r["verdict"] == "base" or (r["verdict"] in ("win", "keep") and r["idea"] in kept))]
    return max(cands, key=lambda r: int(r["id"])) if cands else None


def best(cfg, rows, tier):
    cands = [r for r in live_rows(rows) if r["tier"] == tier and num(r["score"]) is not None
             and r["verdict"] not in ("invalid", "loss", "reject")]
    if not cands:
        return None
    pick = min if cfg["better"] == "lower" else max
    return pick(cands, key=lambda r: num(r["score"]))


def noise_of(st, tier):
    return (st.get("noise") or {}).get(tier, {}).get("spread")


def spearman(pairs):
    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        out = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            for m in range(i, j + 1):
                out[order[m]] = (i + j) / 2 + 1
            i = j + 1
        return out
    xs, ys = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def agreement(rows, lo, hi):
    """Rank agreement between two tiers over attempts scored at both.
    Returns (rho or None, pair count). An attempt is keyed by its idea, or
    by its evidence when it has no idea."""
    def latest(tier):
        out = {}
        for r in live_rows(rows):
            key = r["idea"] or r["evidence"]
            if r["tier"] == tier and key and num(r["score"]) is not None and r["verdict"] != "invalid":
                out[key] = num(r["score"])
        return out
    a, b = latest(lo), latest(hi)
    pairs = [(a[k], b[k]) for k in a if k in b]
    if len(pairs) < AGREE_MIN_PAIRS:
        return None, len(pairs)
    return spearman(pairs), len(pairs)


def phase_totals(rows, last=TUNE_ROWS):
    timed = [r for r in live_rows(rows) if any(num(r[p]) for p in PHASES)][-last:]
    totals = {p: sum(num(r[p]) or 0.0 for r in timed) for p in PHASES}
    return totals, len(timed)


# ---------------------------------------------------------------- scorer hash

def match_files(patterns):
    base = root()
    out = set()
    for pat in patterns or []:
        if Path(pat).expanduser().is_absolute():  # a scorer kept outside this checkout
            ap = Path(pat).expanduser()
            hits = list(Path(ap.anchor).glob(str(ap.relative_to(ap.anchor)))) if any(c in pat for c in "*?[") else [ap]
        else:
            hits = [p for p in base.glob(pat)] if any(c in pat for c in "*?[") else [base / pat]
        for p in hits:
            if p.is_dir():
                out.update(q for q in p.rglob("*") if q.is_file() and "__pycache__" not in q.parts)
            elif p.is_file():
                out.add(p)
    return sorted(out)


def scorer_hash(cfg):
    files = match_files((cfg.get("scorer") or {}).get("paths"))
    h = hashlib.sha256()
    for p in files:
        try:
            name = p.relative_to(root()).as_posix()
        except ValueError:
            name = p.as_posix()
        h.update(name.encode())
        h.update(b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return (h.hexdigest()[:16] if files else ""), len(files)


# ---------------------------------------------------------------- WISH, backlog, gates

def wish_missing(lab):
    try:
        text = (lab / "WISH.md").read_text(encoding="utf-8")
    except OSError:
        return list(WISH_FIELDS)
    sec = {m.group(1).strip(): m.group(2) for m in
           re.finditer(r"^## (.+?)\n(.*?)(?=^## |\Z)", text, re.S | re.M)}
    return [f for f in WISH_FIELDS if not clean(sec.get(f, "")) or "TODO" in sec.get(f, "")]


def backlog(lab):
    return table(lab / "BACKLOG.md", BACKLOG_HEAD)


def gates(lab):
    return table(lab / "GATES.md", GATES_HEAD)


def open_gates(lab):
    return [g for g in gates(lab)[1] if g["status"] == "open"]


def rank_key(idea):
    r = num(idea.get("rank"))
    return (r if r is not None else 1e9, idea.get("id", ""))


def file_set(idea):
    return {f for f in re.split(r"[,\s]+", idea.get("files", "")) if f}


def isolated_pair(a, b):
    """Two ideas may run side by side when both name files and none overlap."""
    fa, fb = file_set(a), file_set(b)
    if not fa or not fb:
        return False
    for x in fa:
        for y in fb:
            if x == y or fnmatch.fnmatch(x, y) or fnmatch.fnmatch(y, x) \
                    or x.startswith(y.rstrip("/") + "/") or y.startswith(x.rstrip("/") + "/"):
                return False
    return True


def fan_out_set(ideas, width):
    picked = []
    for idea in sorted(ideas, key=rank_key):
        if all(isolated_pair(idea, p) for p in picked):
            picked.append(idea)
        if len(picked) >= width:
            break
    return picked if len(picked) >= 2 else []


# ---------------------------------------------------------------- stage checks

def has_published(lab):
    """True when a golden entry comes from a published example, not a pin.
    Read from the index, so labs made before pins existed are judged right."""
    return any(g.get("source") != "pinned" for g in read_golden(lab))


def check_preflight(lab, cfg, st, rows):
    roster = read_json(lab / "roster.json", None)
    if not roster:
        return "no roster.json", f"genie preflight --track {cfg['track']}"
    if time.time() - roster.get("epoch", 0) > ROSTER_MAX_AGE and not rows:
        return "roster.json is older than a day", f"genie preflight --track {cfg['track']}"
    missing = [s for s, ok in (roster.get("secrets") or {}).items() if not ok]
    if missing:
        return f"secret files missing: {', '.join(missing)}", "ask the human through a gate, kind secret"
    return None


def check_plan(lab, cfg, st, rows):
    miss = wish_missing(lab)
    if miss:
        return (f"WISH.md fields not filled: {', '.join(miss)}",
                f"fill them in {lab / 'WISH.md'} from the brief. Remove every TODO")
    return None


def check_scorer(lab, cfg, st, rows):
    t = cfg["track"]
    if not tier_cmd(cfg, "T2"):
        return "no T2 score command", f"write [tiers.T2] cmd in {lab / 'genie.toml'}"
    h, n = scorer_hash(cfg)
    if not n:
        return "scorer paths are empty or match no file", f"list them under [scorer] paths in {lab / 'genie.toml'}"
    g = st.get("golden") or {}
    if not (g.get("ok") and g.get("hash") == h and g.get("n", 0) >= 1):
        why = "golden replay not passed for this scorer" if g else "no golden entry reproduced yet"
        return why, (f"genie golden add <entry> --score <known> --track {t}, "
                     f"then genie golden --track {t}")
    o = st.get("order") or {}
    if not (o.get("ok") and o.get("hash") == h):
        return "order test not passed for this scorer", \
            f"genie order <worse entry> <base entry> [<better entry>] --track {t}"
    if not has_published(lab) and len(o.get("entries") or []) < 3:
        # No published example reproduces, so the golden set only proves the
        # scorer is stable. The order test then carries all the trust.
        return ("golden entries are pinned only, so the order test needs 3 or more entries "
                "that the board has ranked"), \
            f"genie order <worst> <middle> <best> --track {t}, in board order"
    if noise_of(st, "T2") is None:
        return "noise not measured at T2", \
            f"genie score --tier T2 --entry <base entry> --runs 5 --noise --track {t}"
    f = st.get("frozen") or {}
    if f.get("hash") != h:
        why = "scorer changed since the freeze" if f else "scorer not frozen"
        return why, f"genie freeze --track {t}"
    return None


def check_baseline(lab, cfg, st, rows):
    t = cfg["track"]
    if not reference(rows, "T2"):
        return "no base row at T2", f"genie score --tier T2 --entry <base entry> --base --track {t}"
    if not best(cfg, rows, "T3"):
        return "no board score in the ledger", \
            ("queue a submit gate, then genie log --tier T3 --score <board> "
             f"--verdict base --evidence <commit> --track {t}")
    return None


def check_scout(lab, cfg, st, rows):
    ideas = [i for i in backlog(lab)[1] if clean(i["first test"])]
    if len(ideas) < 3:
        return (f"backlog has {len(ideas)} ideas with a first test, needs 3",
                f"run the scout skill, then genie idea add ... --track {cfg['track']}")
    return None


CHECKS = (check_preflight, check_plan, check_scorer, check_baseline, check_scout)


def stage_of(lab, cfg, st, rows):
    """(stage number, failing check text, fix). Stage 5 has no failing check."""
    for n, fn in enumerate(CHECKS):
        res = fn(lab, cfg, st, rows)
        if res:
            return n, res[0], res[1]
    return 5, "", ""


def loop_job(lab, cfg, st, rows):
    """The one next job inside the loop. Returns (job, why, do)."""
    t = cfg["track"]
    live = live_rows(rows)
    rho, n = agreement(rows, "T2", "T3")
    if rho is not None and rho < AGREE_FLOOR:
        return ("fix the scorer", f"local T2 and board T3 rank {n} attempts differently (rho {rho:.2f}). "
                "This outranks every idea", "find what the board measures that T2 does not. Then golden and freeze")
    rho, n = agreement(rows, "T1", "T2")
    if rho is not None and rho < AGREE_FLOOR:
        return ("rebuild T1", f"T1 and T2 rank {n} attempts differently (rho {rho:.2f})",
                "pick the T1 subset from cases that separated past entries")
    totals, timed = phase_totals(rows)
    total = sum(totals.values())
    recent = live[-TUNE_ROWS:]
    if timed >= TUNE_MIN_ROWS and total > 0 and not any(r["idea"].startswith("tune") for r in recent):
        phase, secs = max(totals.items(), key=lambda kv: kv[1])
        if secs / total > TUNE_SHARE:
            return (f"tune the loop: cut {phase[2:]} time",
                    f"{phase[2:]} is {100 * secs / total:.0f}% of the last {timed} iterations",
                    f"genie idea add \"tune: cut {phase[2:]} time\" --rank 0 --track {t}")
    scouted = st.get("scouted_at_row", 0)
    outcome = {}  # one outcome per attempt, in order of its last row
    for r in live:
        if int(r["id"]) <= scouted or r["verdict"] not in ("keep", "reject", "loss", "win"):
            continue
        key = r["idea"] or f"row{r['ref'] or r['id']}"
        was = outcome.pop(key, None)
        outcome[key] = "keep" if "keep" in (was, r["verdict"]) else \
            ("fail" if r["verdict"] in ("reject", "loss") else "pending")
    concluded = [v for v in outcome.values() if v != "pending"][-STALL_REJECTS:]
    if len(concluded) == STALL_REJECTS and all(v == "fail" for v in concluded):
        return ("scout again", f"{STALL_REJECTS} rejects in a row",
                f"run the scout skill, ask for a second opinion, then genie scouted --track {t}")
    keeps = [from_iso(r["time"]) for r in live if r["verdict"] in ("keep", "base")]
    since = [r for r in live if keeps and from_iso(r["time"]) > max(keeps) and int(r["id"]) > scouted]
    if keeps and len(since) >= 2 and from_iso(since[-1]["time"]) - max(keeps) > STALL_SECONDS:
        return ("scout again", "an hour of attempts with no gain",
                f"run the scout skill, ask for a second opinion, then genie scouted --track {t}")
    ideas = backlog(lab)[1]
    opened = sorted((i for i in ideas if i["status"] == "open"), key=rank_key)
    running = [i for i in ideas if i["status"] == "running"]
    width = int(cfg.get("width", 1) or 1)
    if opened and width > 1:
        fan = fan_out_set(opened, width - len(running)) if width - len(running) >= 2 else []
        if fan:
            ids = " ".join(i["id"] for i in fan)
            return (f"fan out {ids}", "their briefs name disjoint files",
                    f"genie brief <id> --track {t} for each, one worktree per idea")
    if opened:
        i = opened[0]
        return (f"try {i['id']}: {i['idea']}", f"top of the backlog. First test: {i['first test'] or 'none written'}",
                f"genie idea start {i['id']} --track {t}")
    if running:
        ids = ", ".join(i["id"] for i in running)
        return (f"finish {ids}", "ideas are running and none is open", "score them: genie score ...")
    if any(i["status"] == "gated" for i in ideas):
        return ("wait on a gate", "every remaining idea waits on the human", f"genie gate list --track {t}")
    return ("scout again", "the backlog is empty", f"run the scout skill, then genie scouted --track {t}")


def fmt(x):
    v = num(x)
    return "none" if v is None else f"{v:g}"


def situation(track):
    """Everything `next` prints, as a dict. One read of the lab."""
    lab = need_lab(track)
    cfg = load_cfg(lab)
    cfg.setdefault("track", track)
    st, rows = load_state(lab), read_ledger(lab)
    stage, failing, fix = stage_of(lab, cfg, st, rows)
    job = (failing, "", fix) if stage < 5 else loop_job(lab, cfg, st, rows)
    b2, b3 = best(cfg, rows, "T2"), best(cfg, rows, "T3")
    ref = reference(rows, "T2")
    return {"track": track, "lab": lab, "cfg": cfg, "state": st, "rows": rows,
            "stage": stage, "failing": failing, "job": job,
            "base": st.get("base") or (ref or {}).get("base") or (ref or {}).get("evidence") or "",
            "best_t2": b2, "best_t3": b3, "gates": open_gates(lab),
            "ideas": backlog(lab)[1]}


def one_line(track):
    """Short brief for the session start hook. Never raises."""
    try:
        s = situation(track)
    except Exception as exc:
        return f"GENIE lab {track}: cannot read ({exc})"
    job = s["job"][0]
    g = f", {len(s['gates'])} gates wait on the human" if s["gates"] else ""
    return (f"GENIE lab {track}: stage {s['stage']} {STAGES[s['stage']]}. "
            f"Best local {fmt((s['best_t2'] or {}).get('score'))}, "
            f"best board {fmt((s['best_t3'] or {}).get('score'))}{g}. "
            f"Next: {job}. Full picture: `genie next --track {track}`")


def render_next(track):
    s = situation(track)
    cfg, st = s["cfg"], s["state"]
    out = [f"GENIE {track}: stage {s['stage']} {STAGES[s['stage']]} (better is {cfg['better']})"]
    passed = ", ".join(STAGES[:s["stage"]]) or "none"
    out.append(f"Stages passed: {passed}")
    n2 = noise_of(st, "T2")
    out.append(f"Base: {s['base'] or 'not set'} | best local T2: {fmt((s['best_t2'] or {}).get('score'))} | "
               f"best board T3: {fmt((s['best_t3'] or {}).get('score'))} | "
               f"T2 noise: {'not measured' if n2 is None else f'{n2:g}'}")
    gold = st.get("golden") or {}
    if gold.get("ok") and not has_published(s["lab"]):
        rho, k = agreement(s["rows"], "T2", "T3")
        out.append("Scorer trust: pinned only, no published example reproduces. The board is the judge. "
                   + (f"T2 against board agreement {rho:.2f} over {k} attempts"
                      if rho is not None else f"T2 against board: {k} attempts scored at both, needs {AGREE_MIN_PAIRS}"))
    opened = [i for i in s["ideas"] if i["status"] == "open"]
    running = [i for i in s["ideas"] if i["status"] == "running"]
    out.append(f"Ledger rows: {len(s['rows'])} | ideas open: {len(opened)}, running: {len(running)}")
    if s["gates"]:
        out.append("Waiting on the human: " + "; ".join(f"{g['id']} {g['kind']}: {g['what']}" for g in s["gates"]))
    job, why, do = s["job"]
    if s["stage"] < 5:
        out.append(f"Failing check: {job}")
    else:
        out.append(f"Job: {job}")
        if why:
            out.append(f"Why: {why}")
    out.append(f"Do: {do}")
    return "\n".join(out)


# ---------------------------------------------------------------- scaffold

WISH_TEMPLATE = """---
date: {date}
tags: [genie, {track}]
type: wish
summary: Problem card for the {track} genie lab
---
# Wish: {track}

Back to [[{hub}]].

{wish}

## Problem
{problem}

## Scoring rule
{scoring}

## Limits
{limits}

## Done when
{done}

## Rules
{rules}
"""

BACKLOG_PRE = """# Backlog: {track}

One row per idea. Lowest rank goes first. `files` lists the paths the idea
touches, comma separated. Two ideas with disjoint files may run side by side.
Status is one of open, running, gated, kept, rejected.
"""

GATES_PRE = """# Waiting on the human: {track}

Pushes, submissions, secrets and spending wait here while other work goes on.
Only the human answers, with `/genie:approve <id>` or `/genie:deny <id>`.
Scope `once` allows one action. Scope `standing` allows it until revoked.
Status is open, approved, denied, used, standing or revoked.
"""

COMMON_BRIEF = """# Rules every worker reads first

- Read `../WISH.md`, then your brief. Do that task and nothing else
- Work only in your own worktree. One writer per folder
- A solver never edits a scorer path. A scorer task never edits a solution path
- Never push, submit, spend or paste a secret. Add a gate row and keep working:
  `genie gate add --kind push "what and why" --track {track}`
- Score through genie, never by hand: `genie score --tier T0 --entry <path> --idea <id> --track {track}`
- Report back with the ledger row id and a commit hash. No prose claims
- Commit and style rules come from the repo's own CLAUDE.md or AGENTS.md
"""


def scaffold(lab, track, better_dir, wish_text, fields, force=False):
    if (lab / "genie.toml").exists() and not force:
        raise GenieError(f"{lab} already holds a lab. Use `genie next --track {track}`")
    for d in ("briefs", "golden", "logs"):
        (lab / d).mkdir(parents=True, exist_ok=True)

    def put(name, text):
        if not (lab / name).exists():
            (lab / name).write_text(text, encoding="utf-8")

    put("genie.toml", TOML_TEMPLATE.format(track=track, better=better_dir))
    put("WISH.md", WISH_TEMPLATE.format(
        date=time.strftime("%Y-%m-%d"), track=track, wish=wish_text, hub=genie_paths.config()["hub"],
        **{k: fields.get(k) or "TODO" for k in ("problem", "scoring", "limits", "done", "rules")}))
    if not (lab / "BACKLOG.md").exists():
        md_write(lab / "BACKLOG.md", BACKLOG_PRE.format(track=track).splitlines() + [""], BACKLOG_HEAD, [], [])
    if not (lab / "GATES.md").exists():
        md_write(lab / "GATES.md", GATES_PRE.format(track=track).splitlines() + [""], GATES_HEAD, [], [])
    put("briefs/COMMON.md", COMMON_BRIEF.format(track=track))
    put("logs/.gitignore", "*\n!.gitignore\n")
    put(".gitignore", ".locks/\n")
    if not (lab / "ledger.tsv").exists():
        (lab / "ledger.tsv").write_text("\t".join(COLS) + "\n", encoding="utf-8", newline="\n")


def conductor_prompt(track):
    return (f"You are the genie conductor for track `{track}`. Load the `genie:lab` skill and follow it. "
            f"Start with `genie next --track {track}` and do the one job it prints. "
            "Ask it again after every ledger row. Never stop to ask whether to continue. "
            "Pushes, submissions, secrets and spending go to the gate queue.")


def cmd_wish(args):
    lab = lab_dir(args.track)
    scaffold(lab, args.track, args.better, f"> {clean(args.problem)}", {})
    print(f"Lab created at {lab}")
    print("\nConductor prompt, paste it into a fresh session:\n")
    print(conductor_prompt(args.track))
    print("\n" + render_next(args.track))
    return 0


# ---------------------------------------------------------------- adopt

def parse_scores(path):
    """Rows of a track SCORES.md as (time cell, commit, local cell, board score, note)."""
    _, rows, _ = md_read(path)
    out = []
    for r in rows:
        r = r + [""] * (5 - len(r))
        when, commit_cell, local, board, note = r[:5]
        if re.search(r"not (submitted|run|scored)", board, re.I):
            continue
        m = re.match(r"\s*(-?\d+(?:\.\d+)?)", board)
        if not m:
            continue
        c = re.search(r"\b[0-9a-f]{7,40}\b", commit_cell)
        cell = clean(commit_cell).replace("`", "")
        bare = c and len(cell) <= len(c.group(0)) + 2
        out.append((when, c.group(0) if bare else cell[:70], local, float(m.group(1)), note))
    return out


def state_section(track_dir, name):
    try:
        text = (track_dir / "STATE.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    parts = text.split("<!-- checkpoint -->")
    if len(parts) < 2:
        return ""
    m = re.search(rf"^## {name}\n(.*?)(?=^## |\Z)", parts[1], re.S | re.M)
    return m.group(1).strip() if m else ""


def cmd_adopt(args):
    """Build a lab for a track that is already running. Writes only what the
    track's own files prove. Everything else stays TODO, so `next` lands on
    the first real gap instead of on stage 0."""
    track = args.track
    lab = lab_dir(track)
    tdir = lab.parent
    if not tdir.is_dir():
        raise GenieError(f"{tdir} does not exist")
    brief = tdir / "brief" / "BRIEF.md"
    goal = state_section(tdir, "Goal")
    fields = {
        "problem": f"See `tracks/{track}/brief/BRIEF.md`. TODO: two lines in our own words." if brief.exists() else "",
        "done": (goal + "\n\nTODO: confirm this is still the target.") if goal else "",
        "rules": "The repo's own rules (CLAUDE.md or AGENTS.md) apply. TODO: add the rules of this problem.",
    }
    scaffold(lab, track, args.better,
             f"Adopted on {time.strftime('%Y-%m-%d %H:%M')} from a running track. "
             "Past work is in `../STATE.md`, `../SCORES.md` and the findings notes.", fields)
    cfg = load_cfg(lab)
    wrote = {"board rows": 0, "ideas": 0}
    if not read_ledger(lab):
        scored = parse_scores(tdir / "SCORES.md")
        top = None
        for row in scored:
            if top is None or better(cfg, row[3], top[3]):
                top = row
        for n, (when, commit, local, board, note) in enumerate(scored, 1):
            append_row(lab, worker="adopt", idea=f"past-{n}", base="", tier="T3",
                       score=f"{board:g}", runs="1", verdict="base" if (when, commit, board) ==
                       (top[0], top[1], top[3]) else "info", evidence=commit,
                       note=f"SCORES.md {when}. local then: {local or 'none'}. {note}"[:300])
            wrote["board rows"] += 1
        if top:
            update_state(lab, lambda st: st.update(base=top[1]))
    pre, ideas, post = backlog(lab)
    if not ideas:
        nxt = state_section(tdir, "Next")
        # One idea per list item. A plain paragraph is one idea, never split
        # by sentence, because half of its sentences are context, not ideas.
        items = [clean(m.group(1)) for m in re.finditer(r"^\s*(?:\d+[.)]|[-*])\s+(.+)$", nxt, re.M)]
        steps = items or ([clean(nxt)] if clean(nxt) else [])
        for n, step in enumerate(steps[:8], 1):
            ideas.append({"id": f"I{n}", "rank": str(n), "idea": step[:300], "source": "STATE.md Next",
                          "first test": "", "files": "", "status": "open"})
            wrote["ideas"] += 1
        with mutex(lab):
            save_table(lab / "BACKLOG.md", BACKLOG_HEAD, pre, ideas, post)
    if not (lab / "roster.json").exists():
        cmd_preflight(args)
    hints = [p.relative_to(root()).as_posix() for p in list(tdir.glob("score*.py")) + list(tdir.glob("Makefile"))
             + list(tdir.glob("*/score*.py"))]
    print(f"Adopted {track}. Lab at {lab}")
    print(f"Wrote {wrote['board rows']} board rows from SCORES.md and {wrote['ideas']} backlog ideas from STATE.md.")
    print("Left for the conductor: score commands in genie.toml, scorer paths, WISH.md TODO fields, "
          "a first test per idea.")
    if hints:
        print("Possible scorer files: " + ", ".join(hints[:6]))
    print("\n" + render_next(track))
    return 0


# ---------------------------------------------------------------- next, preflight

def cmd_next(args):
    if args.track:
        print(render_next(args.track))
        return 0
    found = labs()
    if not found:
        print("No genie lab yet. `genie adopt --track <slug> --better lower|higher` "
              "for a running track.")
        return 0
    for lab in found:
        print(one_line(lab.parent.name))
    return 0


def cmd_preflight(args):
    lab = need_lab(args.track)
    cfg = load_cfg(lab)
    tools = {t: shutil.which(t) for t in (cfg.get("preflight") or {}).get("tools", [])}
    secrets = {s: (root() / s).exists() or Path(s).expanduser().exists()
               for s in (cfg.get("secrets") or {}).get("files", [])}
    try:
        free_gb = shutil.disk_usage(root()).free / 1e9
    except OSError:
        free_gb = 0.0
    roster = {"time": now_iso(), "epoch": time.time(), "tools": tools, "secrets": secrets,
              "cores": os.cpu_count(), "free_gb": round(free_gb, 1),
              "quota": "not read, no command reports it"}
    write_json(lab / "roster.json", roster)
    for t, p in tools.items():
        print(f"  {t:<10} {'found ' + p if p else 'MISSING'}")
    for s, ok in secrets.items():
        print(f"  secret {s}: {'present' if ok else 'MISSING'}")
    print(f"  cores {roster['cores']}, free disk {roster['free_gb']} GB, quota not read")
    print("A found binary is not proof the agent answers. Ping the ones you plan to use.")
    return 0


# ---------------------------------------------------------------- score

def parse_score(cfg, text):
    rx = clean((cfg.get("parse") or {}).get("regex", ""))
    if rx:
        hits = re.findall(rx, text, re.M)
        if not hits:
            return None
        h = hits[-1]
        return num(h[0] if isinstance(h, tuple) else h)
    for line in reversed(text.splitlines()):
        if line.strip():
            m = re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", line)
            return num(m[-1]) if m else None
    return None


def run_cmd(cmd, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, shell=True, cwd=str(root()), capture_output=True, text=True,
                           errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + ("\n--- stderr ---\n" + p.stderr if p.stderr else ""), \
            p.stdout or "", time.time() - t0
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        return 124, out + f"\n--- timed out after {timeout} s ---\n", "", time.time() - t0


def take_clock(lab, worker, idea, t_start):
    """Edit and wait seconds since this worker's last row. Clears them."""
    out = {}

    def fn(st):
        clocks = st.setdefault("clocks", {})
        c = clocks.get(worker) or {}
        wait = float(c.get("wait", 0.0))
        since = c.get("since") if c.get("idea") == idea else None
        edit = max(0.0, t_start - since - wait) if since else 0.0
        out.update(edit=edit, wait=wait)
        clocks[worker] = {"idea": idea, "since": time.time(), "wait": 0.0}
    update_state(lab, fn)
    return out["edit"], out["wait"]


def score_entry(lab, cfg, tier, entry, runs):
    """Run the tier command `runs` times. Returns (scores, log text, t_build, t_score, ok)."""
    cmd = tier_cmd(cfg, tier)
    if not cmd:
        raise GenieError(f"no command for {tier} in genie.toml")
    cmd = cmd.replace("{entry}", str(entry))
    timeout = int(cfg.get("timeout", 3600) or 3600)
    log, t_build = [], 0.0
    build = clean((cfg.get("build") or {}).get("cmd", "")).replace("{entry}", str(entry))
    if build:
        code, text, _, t_build = run_cmd(build, timeout)
        log.append(f"$ {build}\n{text}\n[exit {code}, {t_build:.1f} s]\n")
        if code != 0:
            return [], "\n".join(log), t_build, 0.0, False
    scores, t_score = [], 0.0
    for i in range(runs):
        code, text, stdout, secs = run_cmd(cmd, timeout)
        t_score += secs
        log.append(f"$ {cmd}   (run {i + 1} of {runs})\n{text}\n[exit {code}, {secs:.1f} s]\n")
        s = parse_score(cfg, stdout) if code == 0 else None
        if s is None:
            return scores, "\n".join(log), t_build, t_score, False
        scores.append(s)
    return scores, "\n".join(log), t_build, t_score, True


def guard_scorer(lab, cfg, st, tier):
    """A score only counts against a frozen scorer. Before the first freeze
    scoring is free, since stage 2 is where the scorer gets built."""
    f = st.get("frozen") or {}
    if not f:
        return
    h, _ = scorer_hash(cfg)
    if h != f.get("hash"):
        raise GenieError("the scorer changed since it was frozen, so this score would not be comparable. "
                         f"Run `genie golden --track {cfg['track']}` and then freeze, "
                         "or restore the scorer files")


def entry_path(entry):
    """A relative entry named from a linked worktree means the file in that
    worktree. Commands run in the main tree, so make it absolute."""
    here = Path.cwd() / entry
    if not Path(entry).is_absolute() and here.exists() and genie_paths.checkout(Path.cwd()) != root():
        return here.resolve().as_posix()
    return entry


def cmd_score(args):
    lab = need_lab(args.track)
    cfg = load_cfg(lab)
    cfg.setdefault("track", args.track)
    st = load_state(lab)
    if not args.noise and not args.unfrozen:
        guard_scorer(lab, cfg, st, args.tier)
    worker = worker_name(args)
    t_start = time.time()
    t_edit, t_wait = take_clock(lab, worker, args.idea or "", t_start)
    scores, log, t_build, t_score, ok = score_entry(lab, cfg, args.tier, entry_path(args.entry), args.runs)
    rows = read_ledger(lab)
    ref = reference(rows, args.tier)
    med = statistics.median(scores) if scores else None
    spread = statistics.stdev(scores) if len(scores) > 1 else None
    noise = noise_of(st, args.tier)
    note = clean(args.note)
    if not ok:
        verdict = "invalid"
    elif args.noise:
        verdict = "info"
        note = (note + " noise run").strip()
    elif args.base or ref is None:
        verdict = "base"
    else:
        g = gain(cfg, med, num(ref["score"]))
        floor = 2 * max(noise or 0.0, spread or 0.0)
        verdict = "win" if g > floor else "loss"
        note = (note + f" gain {g:+g} against row {ref['id']} ({fmt(ref['score'])}), needs over {floor:g}").strip()
        if noise is None:
            note += ", noise not measured"
    row = append_row(lab, worker=worker, idea=args.idea or "", base=st.get("base", ""), tier=args.tier,
                     score="" if med is None else f"{med:g}", runs=str(len(scores)),
                     spread="" if spread is None else f"{spread:g}",
                     t_edit=f"{t_edit:.0f}", t_build=f"{t_build:.0f}", t_score=f"{t_score:.0f}",
                     t_wait=f"{t_wait:.0f}", verdict=verdict, evidence="", note=note)
    logp = lab / "logs" / f"{int(row['id']):04d}-{args.tier}.log"
    logp.parent.mkdir(exist_ok=True)
    logp.write_text(log, encoding="utf-8")
    rel = logp.relative_to(root()).as_posix()
    if args.noise and ok and spread is not None:
        update_state(lab, lambda s: s.setdefault("noise", {}).update(
            {args.tier: {"spread": spread, "runs": len(scores), "median": med, "time": now_iso()}}))
    print(f"row {row['id']}: {args.tier} {verdict}, score {fmt(row['score'])}"
          f"{'' if spread is None else f', spread {spread:g}'} over {len(scores)} runs, "
          f"{t_score:.0f} s scoring. Log: {rel}")
    if note:
        print(f"  {note}")
    if not ok:
        print("  The command failed or printed no score. Read the log.")
        return 1
    if verdict == "win" and args.tier != "T3":
        nxt = TIERS[TIERS.index(args.tier) + 1]
        if nxt == "T3":
            print(f"  A T2 win. Decide with `genie keep {row['id']} --evidence <commit> "
                  f"--track {args.track}`, then queue a submit gate for the board.")
        elif tier_cmd(cfg, nxt):
            print(f"  Promote: score it at {nxt}.")
        else:
            print(f"  {nxt} has no command, so go to T2.")
    elif verdict == "loss":
        print(f"  Stop here. `genie reject {row['id']} --track {args.track}` and drop the worktree.")
    return 0


def cmd_log(args):
    lab = need_lab(args.track)
    load_cfg(lab)
    worker = worker_name(args)
    t_edit, t_wait = (0.0, 0.0)
    if args.tier != "T3":
        t_edit, t_wait = take_clock(lab, worker, args.idea or "", time.time())
    row = append_row(lab, worker=worker, idea=args.idea or "", base=args.base_commit or load_state(lab).get("base", ""),
                     tier=args.tier, score="" if args.score is None else f"{args.score:g}",
                     runs=str(args.runs), spread="" if args.spread is None else f"{args.spread:g}",
                     t_edit=f"{args.t_edit if args.t_edit is not None else t_edit:.0f}",
                     t_build=f"{args.t_build or 0:.0f}", t_score=f"{args.t_score or 0:.0f}",
                     t_wait=f"{args.t_wait if args.t_wait is not None else t_wait:.0f}",
                     verdict=args.verdict, evidence=args.evidence or "", ref=args.ref or "", note=args.note or "")
    print(f"row {row['id']}: {row['tier']} {row['verdict']} {fmt(row['score'])}")
    return 0


def find_row(rows, rid):
    for r in rows:
        if r["id"] == str(rid):
            return r
    raise GenieError(f"no ledger row {rid}")


def set_idea(lab, idea_id, status):
    if not idea_id:
        return
    with mutex(lab):
        pre, ideas, post = backlog(lab)
        for i in ideas:
            if i["id"] == idea_id:
                i["status"] = status
                save_table(lab / "BACKLOG.md", BACKLOG_HEAD, pre, ideas, post)
                return


def cmd_decide(args):
    """keep, reject or supersede a row by appending a new one that points at it."""
    lab = need_lab(args.track)
    cfg = load_cfg(lab)
    rows = read_ledger(lab)
    src = find_row(rows, args.id)
    verdict = args.decision
    if verdict == "keep":
        if not args.evidence:
            raise GenieError("a keep needs --evidence <commit hash or log path>, never prose")
        if src["verdict"] not in ("win", "base") and not args.force:
            raise GenieError(f"row {src['id']} is a {src['verdict']}, not a win. Only the score decides a keep. "
                             "Pass --force with a --note if the human overrode it")
        if src["tier"] != "T2" and not args.force:
            raise GenieError(f"row {src['id']} is {src['tier']}. A keep needs a T2 win")
        need = int(cfg.get("keep_runs", 1) or 1)
        if int(src["runs"] or 1) < need and not args.force:
            raise GenieError(f"row {src['id']} has {src['runs'] or 1} runs, a keep needs the median of {need}. "
                             f"Score it again with --runs {need}")
    row = append_row(lab, worker=worker_name(args), idea=src["idea"], base=src["base"], tier=src["tier"],
                     score=src["score"], runs=src["runs"], spread=src["spread"], verdict=verdict,
                     evidence=args.evidence or src["evidence"], ref=src["id"], note=args.note or "")
    if verdict == "keep":
        update_state(lab, lambda st: st.update(base=args.evidence))
        set_idea(lab, src["idea"], "kept")
        print(f"row {row['id']}: kept row {src['id']}. New base is {args.evidence}. "
              "Rescore other winners on this base before merging them.")
    elif verdict == "reject":
        set_idea(lab, src["idea"], "rejected")
        print(f"row {row['id']}: rejected row {src['id']}")
    else:
        print(f"row {row['id']}: row {src['id']} no longer counts")
    return 0


def cmd_scouted(args):
    lab = need_lab(args.track)
    rows = read_ledger(lab)
    last = max((int(r["id"]) for r in rows if r["id"].isdigit()), default=0)
    update_state(lab, lambda st: st.update(scouted_at_row=last, scouted_time=now_iso()))
    print(f"Scout recorded at ledger row {last}. The stall counter restarts.")
    return 0


def cmd_base(args):
    lab = need_lab(args.track)
    update_state(lab, lambda st: st.update(base=args.commit))
    print(f"Base is now {args.commit}")
    return 0


# ---------------------------------------------------------------- time, ledger view

def cmd_time(args):
    lab = need_lab(args.track)
    rows = read_ledger(lab)
    totals, n = phase_totals(rows, args.last)
    total = sum(totals.values())
    if not n or not total:
        print("No timed rows yet. Rows written by `genie score` carry timings.")
        return 0
    print(f"Last {n} timed iterations, {total / n:.0f} s each on average:")
    for p in PHASES:
        share = totals[p] / total
        print(f"  {p[2:]:<6} {totals[p]:>8.0f} s  {100 * share:>3.0f}%{'   <- cut this' if share > TUNE_SHARE else ''}")
    for lo, hi in (("T1", "T2"), ("T2", "T3")):
        rho, k = agreement(rows, lo, hi)
        if rho is None:
            print(f"  {lo} against {hi}: {k} attempts scored at both, needs {AGREE_MIN_PAIRS}")
        else:
            print(f"  {lo} against {hi}: rank agreement {rho:.2f} over {k} attempts"
                  f"{'   <- below ' + str(AGREE_FLOOR) if rho < AGREE_FLOOR else ''}")
    return 0


def cmd_ledger(args):
    lab = need_lab(args.track)
    rows = read_ledger(lab)[-args.last:]
    if not rows:
        print("The ledger is empty.")
        return 0
    cols = ["id", "time", "worker", "idea", "tier", "score", "runs", "spread", "verdict", "evidence", "ref"]
    data = [[(r[c][5:16] if c == "time" else r[c])[:26] for c in cols] for r in rows]
    widths = [max(len(c), *(len(d[i]) for d in data)) for i, c in enumerate(cols)]
    line = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(line.format(*cols))
    for d in data:
        print(line.format(*d))
    return 0


# ---------------------------------------------------------------- golden, order, freeze

GOLDEN_COLS = ["entry", "tier", "score", "source", "added"]


def read_golden(lab):
    try:
        lines = (lab / "golden" / "index.tsv").read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return []
    return [dict(zip(GOLDEN_COLS, l.split("\t"))) for l in lines if l.strip()]


def cmd_golden(args):
    lab = need_lab(args.track)
    cfg = load_cfg(lab)
    if args.action in ("add", "remove"):
        if args.entry is None or (args.action == "add" and args.score is None):
            raise GenieError("golden add needs <entry> and --score <known score>. golden remove needs <entry>")
        with mutex(lab):
            path = lab / "golden" / "index.tsv"
            path.parent.mkdir(exist_ok=True)
            entry = clean(args.entry)
            old = read_golden(lab)
            # add replaces the same entry at the same tier. remove without
            # --tier drops the entry at every tier.
            tier = args.tier or ("T2" if args.action == "add" else None)
            kept = [g for g in old if not (g["entry"] == entry and (tier is None or g["tier"] == tier))]
            dropped = len(old) - len(kept)
            if args.action == "add":
                kept.append({"entry": entry, "tier": tier, "score": f"{args.score:g}",
                             "source": clean(args.source), "added": now_iso()})
            tmp = path.with_suffix(".tmp")
            tmp.write_text("\n".join(["\t".join(GOLDEN_COLS)] +
                                     ["\t".join(g.get(c, "") for c in GOLDEN_COLS) for g in kept]) + "\n",
                           encoding="utf-8", newline="\n")
            os.replace(tmp, path)
        if args.action == "add":
            print(f"Golden entry {'replaced' if dropped else 'added'}: {entry} scores {args.score:g} "
                  f"at {tier} ({args.source})")
        else:
            print(f"Removed {dropped} golden row(s) for {entry}. Run `golden` again before a freeze.")
        return 0
    gold = read_golden(lab)
    if not gold:
        raise GenieError("no golden entries. Add published examples first: "
                         f"`genie golden add <entry> --score <known> --track {args.track}`. "
                         "If the board is the only true scorer, pin your own scores with --source pinned. "
                         "A pin proves the scorer is stable, not that it is right")
    st = load_state(lab)
    tol0 = float(cfg.get("golden_tol", 0.0) or 0.0)
    bad = 0
    for g in gold:
        scores, log, _, secs, ok = score_entry(lab, cfg, g["tier"], g["entry"], 1)
        tol = tol0 + 2 * (noise_of(st, g["tier"]) or 0.0)
        want = num(g["score"])
        got = scores[0] if ok and scores else None
        good = got is not None and abs(got - want) <= tol + 1e-12
        bad += 0 if good else 1
        print(f"  {'ok  ' if good else 'FAIL'} {g['entry']}: want {want:g}, got {fmt(got)}, "
              f"allowed gap {tol:g}, {secs:.0f} s")
        if not good:
            (lab / "logs" / "golden-fail.log").write_text(log, encoding="utf-8")
    h, _ = scorer_hash(cfg)
    published = sum(1 for g in gold if g.get("source") != "pinned")
    update_state(lab, lambda s: s.update(golden={"ok": bad == 0, "hash": h, "n": len(gold),
                                                 "published": published, "time": now_iso()}))
    if bad:
        print(f"{bad} of {len(gold)} golden entries do not reproduce. The scorer change is rejected. "
              "Fix or restore the scorer.")
        return 1
    print(f"All {len(gold)} golden entries reproduce.")
    return 0


def cmd_order(args):
    """The scorer must rank a worse entry, the base and a better entry in that order."""
    lab = need_lab(args.track)
    cfg = load_cfg(lab)
    got = []
    for e in args.entries:
        scores, _, _, secs, ok = score_entry(lab, cfg, args.tier, e, 1)
        if not ok:
            raise GenieError(f"{e} did not score. Run it with `genie score` and read the log")
        got.append(scores[0])
        print(f"  {e}: {scores[0]:g} ({secs:.0f} s)")
    ok = all(better(cfg, got[i + 1], got[i]) for i in range(len(got) - 1))
    h, _ = scorer_hash(cfg)
    update_state(lab, lambda s: s.update(order={"ok": ok, "hash": h, "entries": args.entries,
                                                "scores": got, "time": now_iso()}))
    print("Order holds: each entry beats the one before it." if ok else
          "ORDER FAILS. The scorer cannot tell worse from better. Do not iterate on it.")
    return 0 if ok else 1


def cmd_freeze(args):
    lab = need_lab(args.track)
    cfg = load_cfg(lab)
    h, n = scorer_hash(cfg)
    if not n:
        raise GenieError("[scorer] paths in genie.toml match no file")
    st = load_state(lab)
    for key, label in (("golden", "golden replay"), ("order", "order test")):
        rec = st.get(key) or {}
        if not (rec.get("ok") and rec.get("hash") == h):
            raise GenieError(f"freeze refused. The {label} has not passed for this exact scorer. "
                             f"Run `genie {key} ... --track {args.track}` first")
    update_state(lab, lambda s: s.update(frozen={"hash": h, "files": n, "time": now_iso()}))
    print(f"Scorer frozen: {n} files, hash {h}. Scores are refused if these files change.")
    return 0


# ---------------------------------------------------------------- ideas, briefs

def cmd_idea(args):
    lab = need_lab(args.track)
    if args.action == "list":
        _, ideas, _ = backlog(lab)
        for i in sorted(ideas, key=rank_key):
            print(f"  {i['id']:<4} rank {i['rank'] or '-':<3} {i['status']:<9} {i['idea'][:80]}"
                  f"{'' if i['first test'] else '   (no first test)'}")
        if not ideas:
            print("The backlog is empty.")
        return 0
    with mutex(lab):
        pre, ideas, post = backlog(lab)
        if args.action == "add":
            if not args.text:
                raise GenieError("idea add needs the idea text")
            new = {"id": next_id(ideas, "I"), "rank": str(args.rank if args.rank is not None else len(ideas) + 1),
                   "idea": args.text, "source": args.source or "", "first test": args.test or "",
                   "files": args.files or "", "status": "open"}
            ideas.append(new)
            msg = f"{new['id']} added at rank {new['rank']}"
        else:
            hit = next((i for i in ideas if i["id"] == args.text), None)
            if not hit:
                raise GenieError(f"no idea {args.text}")
            if args.action == "start":
                hit["status"] = "running"
                msg = f"{hit['id']} is running. The edit clock started."
            else:
                if args.status and args.status not in IDEA_STATUS:
                    raise GenieError(f"status must be one of {', '.join(IDEA_STATUS)}")
                for field, val in (("status", args.status), ("rank", args.rank), ("first test", args.test),
                                   ("files", args.files), ("source", args.source)):
                    if val is not None:
                        hit[field] = str(val)
                msg = f"{hit['id']} updated"
        save_table(lab / "BACKLOG.md", BACKLOG_HEAD, pre, ideas, post)
    if args.action == "start":
        w = worker_name(args)
        update_state(lab, lambda st: st.setdefault("clocks", {}).update(
            {w: {"idea": args.text, "since": time.time(), "wait": 0.0}}))
    print(msg)
    return 0


BRIEF_TEMPLATE = """# Brief {id}: {idea}

Read `COMMON.md` first. Role: {role}.

| Field | Value |
|---|---|
| Track | {track} |
| Base commit | {base} |
| Worktree | `{worktree}` |
| Branch | `{branch}` |
| Files you may touch | {files} |
| First test | {test} |
| Source | {source} |

## Task
{idea}

## Mechanism
TODO by the conductor: why this should move the score, in one or two lines.

## Done when
- The first test above ran and its result is in the ledger
- `genie score --tier T0 --entry <path> --idea {id} --track {track}` passed, then T1, then T2
- You reported the ledger row id and your commit hash to the conductor

## Stop when
- A tier is lost. Report the row id and stop. Do not tune around a loss
- You would need a file outside the list above. Report that instead of editing it
"""


def cmd_brief(args):
    lab = need_lab(args.track)
    _, ideas, _ = backlog(lab)
    idea = next((i for i in ideas if i["id"] == args.id), None)
    if not idea:
        raise GenieError(f"no idea {args.id}")
    st = load_state(lab)
    base = st.get("base") or "HEAD"
    foreign = ""
    if base != "HEAD":
        try:
            known = subprocess.run(["git", "-C", str(root()), "cat-file", "-e", f"{base.split()[0]}^{{commit}}"],
                                   capture_output=True, timeout=10).returncode == 0
        except Exception:
            known = True
        if not known:
            foreign = base
    branch = f"genie/{args.track}/{args.id}"
    wt = args.worktree or str(root().parent / f"{root().name}-{args.track}-{args.id}")
    path = lab / "briefs" / f"{args.id}.md"
    if not path.exists() or args.force:
        path.write_text(BRIEF_TEMPLATE.format(
            id=args.id, idea=idea["idea"], role=args.role, track=args.track, base=base, worktree=wt,
            branch=branch, files=idea["files"] or "TODO", test=idea["first test"] or "TODO",
            source=idea["source"] or "none"), encoding="utf-8")
    if args.worktree and Path(args.worktree).is_dir():
        write_json(Path(args.worktree) / ".genie-role", {"role": args.role, "track": args.track, "idea": args.id})
        print(f"Role file written to {Path(args.worktree) / '.genie-role'}")
    rel = lab.relative_to(root()).as_posix()
    print(f"Brief at {path}")
    print("Start the worker like this:")
    if foreign:
        print(f"  The base {foreign} is not a commit in this repo. Make the worktree in the repo that has it,")
        print("  or copy that entry in, and pass the folder with --worktree.")
    else:
        print(f"  git worktree add \"{wt}\" -b {branch} {base}")
    print(f"  genie brief {args.id} --track {args.track} --role {args.role} --worktree \"{wt}\"")
    print(f"  then open a session in that folder with GENIE_WORKER={args.id} and say: "
          f"read {rel}/briefs/COMMON.md and {rel}/briefs/{args.id}.md, then do the brief.")
    return 0


# ---------------------------------------------------------------- gates

def gate_home(track):
    """Gates of a lab when a track is named and has one, else the project queue."""
    if track and (lab_dir(track) / "genie.toml").exists():
        return lab_dir(track)
    home = root() / ".genie"
    if not (home / "GATES.md").exists():
        home.mkdir(parents=True, exist_ok=True)
        md_write(home / "GATES.md", GATES_PRE.format(track="whole project").splitlines() + [""], GATES_HEAD, [], [])
    return home


def gate_homes():
    found = [lab for lab in labs() if (lab / "GATES.md").exists()]
    home = root() / ".genie"
    return ([home] if (home / "GATES.md").exists() else []) + found


def gate_prefix(home):
    """Project gates are G1. Lab gates carry the track, so an id names one gate."""
    return "G" if home.name == ".genie" else f"{home.parent.name}-G"


def cmd_gate(args):
    if args.action == "list":
        homes = [gate_home(args.track)] if args.track else gate_homes()
        n = 0
        for home in homes:
            for g in gates(home)[1]:
                if args.all or g["status"] in ("open", "approved", "standing"):
                    n += 1
                    print(f"  {g['id']:<14} {g['status']:<9} {g['kind']:<7} {g['scope']:<9} {g['what'][:90]}"
                          f"{'  -> ' + g['answer'] if g['answer'] else ''}")
        if not n:
            print("Nothing waits on the human.")
        return 0
    if args.action == "add":
        if not args.text:
            raise GenieError("gate add needs the text: what you want to do and why")
        if args.kind not in GATE_KINDS:
            raise GenieError(f"kind must be one of {', '.join(GATE_KINDS)}")
        home = gate_home(args.track)
        with mutex(home):
            pre, gs, post = gates(home)
            g = {"id": next_id(gs, gate_prefix(home)), "opened": now_iso()[5:16], "kind": args.kind,
                 "scope": "standing" if args.standing else "once", "what": args.text,
                 "asked by": worker_name(args), "status": "open", "answer": ""}
            gs.insert(0, g)
            save_table(home / "GATES.md", GATES_HEAD, pre, gs, post)
        print(f"{g['id']} queued. Keep working on something else. The human answers it, you do not.")
        return 0
    # done: the human's answer. The PreToolUse rule keeps agents away from it.
    for home in gate_homes():
        with mutex(home):
            pre, gs, post = gates(home)
            g = next((x for x in gs if x["id"].lower() == clean(args.text).lower()), None)
            if not g:
                continue
            ans = clean(args.answer or "yes")
            if ans.lower().startswith(("no", "den", "revoke")):
                g["status"] = "revoked" if g["status"] == "standing" else "denied"
            else:
                g["status"] = "standing" if g["scope"] == "standing" else "approved"
            g["answer"] = f"{ans} ({now_iso()[5:16]})"
            save_table(home / "GATES.md", GATES_HEAD, pre, gs, post)
            print(f"{g['id']} {g['status']}: {g['kind']}, {g['what']}")
            return 0
    raise GenieError(f"no gate {args.text}. `genie gate list` shows the open ones")


def take_approval(kind):
    """Use an approval of this kind from any queue. A standing one is not used up."""
    for home in gate_homes():
        try:
            with mutex(home, wait=2.0):
                pre, gs, post = gates(home)
                for g in gs:
                    if g["kind"] == kind and g["status"] == "standing":
                        return True
                for g in gs:
                    if g["kind"] == kind and g["status"] == "approved":
                        g["status"] = "used"
                        save_table(home / "GATES.md", GATES_HEAD, pre, gs, post)
                        return True
        except GenieError:
            continue
    return False


# ---------------------------------------------------------------- locks

def cmd_lock(args):
    lab = need_lab(args.track)
    d = lab / ".locks"
    d.mkdir(exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9_-]", "_", args.name)
    path = d / f"{name}.lock"
    me = worker_name(args)
    if args.action == "unlock":
        rec = read_json(path, None)
        if rec is None:
            print(f"{name} was not locked")
            return 0
        if rec.get("holder") != me and not args.force:
            raise GenieError(f"{name} is held by {rec.get('holder')}, not {me}")
        path.unlink()
        print(f"{name} released")
        return 0
    if args.action == "locks":
        found = sorted(d.glob("*.lock"))
        for p in found:
            rec = read_json(p, {})
            print(f"  {p.stem:<16} {rec.get('holder', '?'):<14} {(time.time() - rec.get('epoch', 0)) / 60:.0f} min")
        if not found:
            print("No locks held.")
        return 0
    t0 = time.time()
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"holder": me, "epoch": time.time(), "time": now_iso()}, fh)
            break
        except FileExistsError:
            rec = read_json(path, {})
            if rec.get("holder") == me:
                print(f"{name} is already yours")
                return 0
            if args.ttl and time.time() - rec.get("epoch", time.time()) > args.ttl:
                path.unlink(missing_ok=True)
                continue
            if time.time() - t0 >= args.wait:
                print(f"{name} is held by {rec.get('holder', '?')} since {rec.get('time', '?')}. "
                      "Do other work and try again.")
                return 1
            time.sleep(1.0)
    waited = time.time() - t0
    if waited >= 1:
        update_state(lab, lambda st: st.setdefault("clocks", {}).setdefault(me, {}).update(
            wait=float((st["clocks"][me]).get("wait", 0.0)) + waited))
    print(f"{name} is yours{f' after {waited:.0f} s' if waited >= 1 else ''}. "
          f"Release it with `genie unlock {name} --track {args.track}`")
    return 0


# ---------------------------------------------------------------- hook rules
#
# These are guardrails for a cooperative agent, not a sandbox. A script that
# pushes from inside another program is not seen here. Use permission rules
# and branch protection for hard limits.

PUSH_RE = re.compile(r"(?:^|[;&|(\s])git\b(?:\s+-\S+(?:\s+\S+)?)*\s+push\b")
ANSWER_RE = re.compile(r"\bgenie(?:\.py)?[\"']?\s+gate\s+done\b")
OWNED = {"GATES.md": "genie gate add", "ledger.tsv": "genie score or genie log", "state.json": "genie commands"}


def role_for(path):
    p = Path(path).resolve()
    for d in p.parents:
        f = d / ".genie-role"
        if f.is_file():
            return read_json(f, {}), d
    return {}, None


def owned_by_script(path):
    p = Path(path).resolve()
    if p.name not in OWNED:
        return False
    return p.parent.name == ".genie" or (p.parent / "genie.toml").exists()


def rule_check(d):
    """PreToolUse rules. Returns a refusal text, or '' to allow."""
    tool = d.get("tool_name", "")
    ti = d.get("tool_input") or {}
    if tool in ("Bash", "PowerShell"):
        cmd = str(ti.get("command", ""))
        if ANSWER_RE.search(cmd):
            return ("genie: only the human answers a gate. Tell them the gate id and that "
                    "`/genie:approve <id>` or `/genie:deny <id>` answers it, then keep working.")
        if PUSH_RE.search(cmd):
            if take_approval("push"):
                return ""
            return ("genie: a push needs the human. Queue it and keep working: "
                    "`genie gate add --kind push \"<what and why>\" [--track <slug>]`. "
                    "Push after the human approves the gate.")
        return ""
    if tool in ("Edit", "Write", "NotebookEdit"):
        target = ti.get("file_path") or ti.get("notebook_path") or ""
        if not target:
            return ""
        if owned_by_script(target):
            return (f"genie: {Path(target).name} is written by the genie command only. "
                    f"Use {OWNED[Path(target).name]}.")
        role, wt = role_for(target)
        if not role or wt is None:
            return ""
        try:
            cfg = load_cfg(need_lab(str(role.get("track", ""))))
            rel = Path(target).resolve().relative_to(wt).as_posix()
        except Exception:
            return ""

        def hit(section):
            for pat in (cfg.get(section) or {}).get("paths", []):
                pat = pat.rstrip("/")
                if fnmatch.fnmatch(rel, pat) or rel == pat or rel.startswith(pat + "/"):
                    return True
            return False
        if role.get("role") == "solver" and hit("scorer"):
            return (f"genie: {rel} belongs to the scorer and this worktree is a solver. "
                    "A solver never edits the scorer. Report the problem to the conductor instead.")
        if role.get("role") == "scorer" and hit("solution"):
            return (f"genie: {rel} is solution code and this worktree is a scorer task. "
                    "A scorer task never edits the solution.")
    return ""


# ---------------------------------------------------------------- status rows

def wish_rows():
    rows = []
    for lab in labs():
        track = lab.parent.name
        try:
            s = situation(track)
            opened = sum(1 for i in s["ideas"] if i["status"] == "open")
            rows.append((track, f"{s['stage']} {STAGES[s['stage']]}", fmt((s["best_t2"] or {}).get("score")),
                         fmt((s["best_t3"] or {}).get("score")), opened, len(s["gates"]), s["job"][0][:70]))
        except Exception as exc:
            rows.append((track, "unreadable", "", "", "", "", str(exc)[:70]))
    return rows
