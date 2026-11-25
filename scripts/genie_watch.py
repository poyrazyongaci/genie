#!/usr/bin/env python3
"""Fleet watcher. No model calls.

    genie watch                 # loop, exit with a report when something needs a look
    genie watch --once          # one pass, print alerts, exit 0, records nothing
    genie watch --once --now    # same with every duration threshold at 0
    genie watch --alive         # is a watcher loop running? (heartbeat age)

Run the loop as a background job. It exits only when it has something to
report, so the agent that started it wakes once per problem and costs
nothing while all is well. Start it again after each report. Each problem is
reported once (state in ~/.claude/genie/watch_seen.json). What to watch is
the `watch` block of .genie/config.json.

Alerts:
  LIMIT    a watched session's last message says it hit a usage limit
  STALL    a session on a track with an open lab job is idle or waiting and
           its transcript has been quiet for stall_minutes
  GONE     a watched session name has no live process for gone_minutes
  LISTENER the hub's board or bridge `wait` is missing for listener_minutes
  HUB      a board or bridge epoch has been the hub's turn for turn_minutes
           while the hub is not busy
  DASH     the dashboard does not answer for five minutes

It reads Claude Code's own session and transcript files. Those formats are not
documented and may change. A file it cannot read is skipped, never guessed.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import genie  # noqa: E402  helpers only, nothing runs at import
import genie_paths  # noqa: E402
from genie_paths import read_json  # noqa: E402

SESSIONS = genie_paths.HOME / ".claude" / "sessions"
SEEN = genie_paths.STATE_HOME / "watch_seen.json"
BEAT = genie_paths.STATE_HOME / "watch.alive"

INTERVAL = 60          # seconds between passes
BRIDGE_EVERY = 5       # the bridge fetch goes over the network, so only every 5th pass
COOLDOWN = 3600        # seconds between repeat LISTENER or DASH alerts for one thing
ALIVE = {"busy", "shell"}

CFG = genie_paths.config()["watch"]
WATCHED = list(CFG["sessions"])
HUB, HUB_CH = CFG["hub_session"], CFG["hub_channel"]
STALL_MIN, GONE_MIN = CFG["stall_minutes"], CFG["gone_minutes"]
LISTEN_MIN, TURN_MIN, DASH_MIN = CFG["listener_minutes"], CFG["turn_minutes"], 5


def pid_alive(pid):
    """Is this process running? Never sends a signal on Windows, where signal 0 is Ctrl+C."""
    try:
        pid = int(pid)
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # query limited information
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259  # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def live_sessions():
    """name -> list of live session records from ~/.claude/sessions/<pid>.json."""
    out = {}
    for p in SESSIONS.glob("*.json"):
        d = read_json(p, {})
        if d.get("pid") and pid_alive(d["pid"]):
            out.setdefault(d.get("name", ""), []).append(d)
    return out


def open_labs():
    try:
        import genie_lab
        return {r[0] for r in genie_lab.wish_rows() if r[1] != "unreadable" and r[6]}
    except Exception:
        return set()


def transcript(sid):
    for d in genie.project_dirs():
        if (d / f"{sid}.jsonl").exists():
            return d / f"{sid}.jsonl"
    hits = list((genie_paths.HOME / ".claude" / "projects").glob(f"*/{sid}.jsonl"))
    return hits[0] if hits else None


LIMIT_RE = re.compile(r"hit your .{0,40}limit", re.I)


def last_text(p):
    """The last assistant text in a transcript (reads the tail only)."""
    try:
        with open(p, "rb") as fh:
            fh.seek(max(0, p.stat().st_size - 400_000))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "assistant":
            continue
        for c in reversed(d.get("message", {}).get("content") or []):
            if isinstance(c, dict) and c.get("type") == "text":
                return c.get("text", "")
    return ""


def check_limits(live):
    """A session that hit a usage limit cannot move until the human switches model."""
    alerts = []
    for name in WATCHED:
        for d in live.get(name, []):
            if d.get("status") in ALIVE:
                continue
            p = transcript(d.get("sessionId", ""))
            text = last_text(p) if p else ""
            if LIMIT_RE.search(text):
                alerts.append((f"limit:{d.get('sessionId')}:{text[:60]}",
                               f"LIMIT {name}: {text.strip()[:120]}"))
    return alerts


def check_stalls(live, labs, now):
    alerts = []
    for name, recs in live.items():
        for d in recs:
            status = d.get("status", "")
            if status in ALIVE:
                continue
            sid = d.get("sessionId", "")
            track = genie.load_state(sid).get("track", "")
            if track not in labs:
                continue
            p = transcript(sid)
            if not p or LIMIT_RE.search(last_text(p)):
                continue  # a usage limit is reported as LIMIT, not as a stall
            _, _, _, kind, last_ts = genie.scan_session(p)
            last = genie.to_epoch(last_ts) or p.stat().st_mtime
            quiet = (now - last) / 60
            if quiet >= STALL_MIN:
                alerts.append((f"stall:{sid}:{last_ts}",
                               f"STALL {name} (track {track}) status={status} quiet {quiet:.0f} min, "
                               f"last message {kind}"))
    return alerts


def since(mem, key, bad, now):
    """Minutes a condition has held, tracked across passes in `mem`."""
    if not bad:
        mem.pop(key, None)
        return -1
    mem.setdefault(key, now)
    return (now - mem[key]) / 60


def check_gone(live, mem, now):
    alerts = []
    for name in WATCHED:
        m = since(mem, f"gone:{name}", name not in live, now)
        if m >= GONE_MIN:
            alerts.append((f"gone:{name}:{int(mem[f'gone:{name}'])}",
                           f"GONE {name} has had no live process for {m:.0f} min"))
    return alerts


def processes():
    """Command lines of running processes, or None where `ps` does not exist."""
    try:
        return subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=10, check=True).stdout
    except Exception:
        return None


def check_listeners(mem, now):
    ps = processes() if CFG["listeners"] else None
    if ps is None:
        return []
    alerts = []
    for tool in CFG["listeners"]:
        up = re.search(rf"\b{tool}(?:\.py)?\s+wait\b.*--ch {re.escape(HUB_CH)}\b", ps) is not None
        m = since(mem, f"listen:{tool}", not up, now)
        if m >= LISTEN_MIN:
            alerts.append((f"listen:{tool}:{int(mem[f'listen:{tool}'])}",
                           f"LISTENER {HUB} `genie {tool} wait` missing for {m:.0f} min"))
    return alerts


def turn_epochs(tool):
    """Epoch numbers that are the hub's turn, from the tool's own brief."""
    try:
        r = subprocess.run([sys.executable, str(SCRIPTS / f"{tool}.py"), "brief", "--ch", HUB_CH],
                           capture_output=True, text=True, timeout=40, cwd=genie_paths.root())
    except Exception:
        return None
    if "waiting on you" not in r.stdout:
        return None
    if "nothing waiting on you" in r.stdout:
        return set()
    return set(re.findall(r"^\s+E(\d+) ", r.stdout, re.M))


def check_turns(mem, now, do_bridge, live):
    """A busy hub is working its queue, so only an idle one is behind."""
    alerts = []
    if any(d.get("status") in ALIVE for d in live.get(HUB, [])):
        return alerts
    for tool in CFG["listeners"]:
        if tool == "bridge" and not do_bridge:
            continue
        eps = turn_epochs(tool)
        if eps is None:
            continue
        known = mem.setdefault(f"turn:{tool}", {})
        for n in list(known):
            if n not in eps:
                del known[n]
        for n in eps:
            known.setdefault(n, now)
            m = (now - known[n]) / 60
            if m >= TURN_MIN:
                alerts.append((f"turn:{tool}:E{n}",
                               f"HUB E{n} on the {tool} has been {HUB}'s turn for {m:.0f}+ min"))
    return alerts


def check_dash(mem, now):
    if not CFG["dash"]:
        return []
    port = genie_paths.config()["dash"]["port"]
    try:
        urllib.request.urlopen(f"http://localhost:{port}/", timeout=5).read(64)
        ok = True
    except Exception:
        ok = False
    m = since(mem, "dash", not ok, now)
    if m >= DASH_MIN:
        return [(f"dash:{int(mem['dash'])}", f"DASH localhost:{port} not answering for {m:.0f} min")]
    return []


def one_pass(mem, seen, n):
    now = time.time()
    live = live_sessions()
    alerts = (check_limits(live) + check_stalls(live, open_labs(), now) + check_gone(live, mem, now)
              + check_listeners(mem, now) + check_turns(mem, now, n % BRIDGE_EVERY == 0, live)
              + check_dash(mem, now))
    def cooled(k):
        """Listener and dashboard outages flap. One alert per hour per thing is enough."""
        base = k.rsplit(":", 1)[0]
        if not base.startswith(("listen:", "dash")):
            return True
        return not any(s.rsplit(":", 1)[0] == base and now - v < COOLDOWN
                       for s, v in seen.items() if s != "_mem")
    fresh = [(k, msg) for k, msg in alerts if k not in seen and cooled(k)]
    for k, _ in fresh:
        seen[k] = now
    return fresh


def save_seen(seen, mem=None):
    cut = time.time() - 2 * 86400
    seen = {k: v for k, v in seen.items() if k != "_mem" and v > cut}
    if mem:
        seen["_mem"] = mem
    genie_paths.STATE_HOME.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(seen), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="genie watch")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--alive", action="store_true")
    ap.add_argument("--now", action="store_true", help="test: zero every duration threshold")
    a = ap.parse_args(argv)
    if not genie_paths.enabled():
        print("This project has not opted in. Run `genie init`.")
        return 1
    if a.now:
        global STALL_MIN, GONE_MIN, LISTEN_MIN, TURN_MIN, DASH_MIN
        STALL_MIN = GONE_MIN = LISTEN_MIN = TURN_MIN = DASH_MIN = 0
    if a.alive:
        try:
            age = time.time() - BEAT.stat().st_mtime
        except OSError:
            print("watcher DEAD (no heartbeat file)")
            return 1
        ok = age < 3 * INTERVAL
        print(f"watcher {'alive' if ok else 'DEAD'} (heartbeat {age:.0f} s ago)")
        return 0 if ok else 1
    seen = read_json(SEEN, {})
    # condition start times survive a restart, so one episode keeps one alert key
    mem, n = seen.pop("_mem", {}), 0
    while True:
        genie_paths.STATE_HOME.mkdir(parents=True, exist_ok=True)
        BEAT.write_text(str(os.getpid()), encoding="utf-8")
        fresh = one_pass(mem, seen, n)
        if fresh or a.once:
            if not a.once:  # a test pass must not use up real alerts
                save_seen(seen, mem)
            stamp = time.strftime("%Y-%m-%d %H:%M %Z")
            print(f"genie watch {stamp}: {len(fresh)} alert(s)")
            for _, msg in fresh:
                print("  " + msg)
            return 0
        n += 1
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
