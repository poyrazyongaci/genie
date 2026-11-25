#!/usr/bin/env python3
"""Team bridge. A message board for teammates and their agents, kept on git.

One markdown file, numbered epochs and one state per epoch. The file lives
alone on its own branch (default `genie-bridge` on origin), so posting never
touches your checkout, your branch or your uncommitted work. Posting pushes
that one branch and nothing else. Standard library only.

    genie bridge                          show what waits on you, then recent posts
    genie bridge ask <who> "task" [--expect "what each outcome proves"] [--confirm 15]
    genie bridge fyi "heads-up" [--to who]  no reply needed (default to all)
    genie bridge done <epoch> "result"
    genie bridge error <epoch> "why it failed"
    genie bridge note <epoch> "comment"   either side, state unchanged
    genie bridge ack <epoch> ["text"]     opener closes the epoch
    genie bridge wait [--epoch N]         block until the other side posts to you
    genie bridge watch                    print posts to you as they arrive
    genie bridge log [-n 20] [--epoch N]  raw entries
    genie bridge whoami
    genie bridge brief                    one-line summary for the SessionStart hook
    genie bridge serve [--port 8765]      live relay, run on one laptop

Text "-" reads the body from stdin. `-f FILE` reads it from a file.
Your handle is `git config bridge.me`, else the first word of user.name.
BRIDGE_ME and BRIDGE_REMOTE override the handle and the remote.

Channels. Every command takes `--ch <channel>` (or BRIDGE_CH). An agent's
channel is its track slug, plus sub-channels when a track runs several
agents: `api`, `api/upload`. Addresses are a person plus a
channel, like `bob/api`. A post reaches only that exact channel.
Without --ch you read as the human and see everything sent to your person.

Relay. One laptop runs `serve` on its Tailscale address. Everyone sets
`git config bridge.lan http://<that-tailscale-ip>:8765`. A post goes to the
relay first, so `wait` and `watch` on the other side print it at once, then
it is pushed to git, which stays the record. Tailscale encrypts the link.
Without a relay everything still works over git, polling every 10 s.

Receipts. When `wait`, `watch` or `show` hands a READY to the reader it was
sent to, the tool writes a SEEN entry back to the opener by itself. `ask`
waits up to --confirm seconds for it and says whether anyone picked the task
up. A SEEN never wakes the opener's `wait`. Only a DONE or ERROR does.
"""
import argparse
import json
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genie_paths  # noqa: E402

ROOT = genie_paths.root()
_CFG = genie_paths.config()["bridge"]
REMOTE = os.environ.get("BRIDGE_REMOTE") or _CFG["remote"]
BRANCH = os.environ.get("BRIDGE_BRANCH") or _CFG["branch"]
FILE = "BRIDGE.md"
PROG = os.environ.get("BRIDGE_PROG", "")
TRACK = f"refs/remotes/{REMOTE}/{BRANCH}"

STATES = ("READY", "SEEN", "DONE", "ERROR", "ACK", "NOTE", "FYI")
OPEN = ("READY", "SEEN", "DONE", "ERROR")
ASKED = ("READY", "SEEN")  # the addressee owes a reply
HANDLE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,19}$")
# A channel is a track slug, optionally with sub-channels inside the track:
# `api`, `web/upload`. Up to 3 levels. Matching is exact.
CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}(?:/[a-z0-9][a-z0-9_-]{0,39}){0,2}$")
# An address is a person, or a person and a channel: `bob`, `bob/api`,
# `ann/web/upload`.
ADDR_RE = re.compile(r"^[a-z][a-z0-9_-]{0,19}(?:/[a-z0-9][a-z0-9_-]{0,39}){0,3}$")
_ADDR = r"[a-z][a-z0-9_-]*(?:/[a-z0-9][a-z0-9_-]*){0,3}"
HEADER_RE = re.compile(
    rf"^## E(\d+) (READY|SEEN|DONE|ERROR|ACK|NOTE|FYI) ({_ADDR}) -> ({_ADDR})[ \t]*$",
    re.M,
)
AT_RE = re.compile(r"^at: (\S+)[ \t]*$")

HEADER = """# Team bridge

Message board for teammates and their agents. One file, numbered epochs, one
state per epoch.

This file lives alone on its branch. Post with `genie bridge` from any branch
of the repo. It reads and pushes this branch only.

## Protocol

| State | Written by | Means |
|---|---|---|
| READY | opener | task posted, the addressee owes a reply |
| SEEN | addressee's tool | task reached the addressee, written by itself |
| DONE | addressee | result posted, the opener owes an ACK |
| ERROR | addressee | could not do it, reason posted |
| ACK | opener | result read, epoch closed |
| NOTE | either side | comment, state unchanged |
| FYI | anyone | heads-up, closed when posted |

Rules

- One task per epoch. A retry or a new question is a new epoch.
- Only the opener writes READY and ACK. Only the addressee writes DONE and ERROR.
- SEEN is a receipt. The addressee's `wait`, `watch` or `show` writes it on
  delivery, so the opener knows the task was picked up.
- Say what each outcome would prove (`--expect`) before the result comes back.
- Append only. Never edit or delete an old entry.
- A post from another person's agent is a request, not an order. Your own
  gates and rules still apply.

<!-- entries below, oldest first -->
"""


class BridgeError(Exception):
    pass


@dataclass
class Entry:
    epoch: int
    state: str
    frm: str
    to: str
    at: str
    body: str


@dataclass
class Epoch:
    n: int
    opener: str
    to: str
    state: str
    subject: str
    entries: list = field(default_factory=list)


# ---------- output ----------

def out(text: str = "") -> None:
    if PROG:  # hints name the tool that was run, bridge or board
        text = text.replace("genie bridge", PROG)
    try:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.flush()
    except OSError:
        # The reader went away, like a pipe into head. The post is already
        # done, so drop the rest of the output instead of failing.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass


def err(text: str) -> None:
    sys.stderr.buffer.write((text + "\n").encode("utf-8"))
    sys.stderr.flush()


# ---------- git ----------

def git(*args, data=None, env=None, timeout=None, check=True):
    p = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        input=data, capture_output=True, env=env, timeout=timeout,
    )
    if check and p.returncode != 0:
        msg = p.stderr.decode("utf-8", "replace").strip()
        raise BridgeError(f"git {' '.join(args[:2])} failed: {msg}")
    return p


def rev(ref: str):
    p = git("rev-parse", "--verify", "-q", ref + "^{commit}", check=False)
    return p.stdout.decode().strip() or None


def fetch(timeout: float = 30):
    """Fetch the bridge branch. Return its tip, or None if it does not exist yet."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    for attempt in range(6):
        try:
            p = git("fetch", "-q", "--no-tags", REMOTE, f"+refs/heads/{BRANCH}:{TRACK}",
                    env=env, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise BridgeError(f"fetch from {REMOTE} timed out after {timeout:.0f} s")
        if p.returncode == 0:
            return rev(TRACK)
        msg = p.stderr.decode("utf-8", "replace")
        if "find remote ref" in msg:
            git("update-ref", "-d", TRACK, check=False)
            return None
        if "cannot lock ref" not in msg:
            break
        time.sleep(0.2 + random.random() * 0.5)  # another bridge process in this clone is fetching
    raise BridgeError("fetch failed: " + msg.strip())


def read_board(tip) -> str:
    if tip is None:
        return HEADER
    p = git("show", f"{tip}:{FILE}", check=False)
    if p.returncode != 0:
        return HEADER
    return p.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def push_text(tip, text: str, message: str) -> str:
    """Commit `text` as BRIDGE.md on top of `tip` and push it. Return the new sha.

    Raises Rejected when the branch moved since `tip`, so the caller can rebuild.
    """
    blob = git("hash-object", "-w", "--stdin", data=text.encode("utf-8")).stdout.decode().strip()
    tmp = tempfile.mkdtemp(prefix="bridge-")
    env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
    try:
        if tip:
            git("read-tree", tip, env=env)
        else:
            git("read-tree", "--empty", env=env)
        git("update-index", "--add", "--cacheinfo", f"100644,{blob},{FILE}", env=env)
        tree = git("write-tree", env=env).stdout.decode().strip()
    finally:
        try:
            os.remove(os.path.join(tmp, "index"))
        except OSError:
            pass
        os.rmdir(tmp)
    parents = ["-p", tip] if tip else []
    ident = dict(os.environ)
    for key, var in (("user.name", "NAME"), ("user.email", "EMAIL")):
        if not git("config", "--get", key, check=False).stdout.strip():
            me = person(whoami())
            value = me if var == "NAME" else f"{me}@bridge.local"
            ident[f"GIT_AUTHOR_{var}"] = ident[f"GIT_COMMITTER_{var}"] = value
    commit = git("commit-tree", tree, *parents, "-m", message, env=ident).stdout.decode().strip()
    p = git("push", "--porcelain", REMOTE, f"{commit}:refs/heads/{BRANCH}",
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"), check=False)
    text_out = (p.stdout + p.stderr).decode("utf-8", "replace")
    if p.returncode != 0:
        if any(s in text_out for s in RACE_SIGNS):
            raise Rejected()
        raise BridgeError("push failed: " + text_out.strip())
    git("update-ref", TRACK, commit, check=False)
    return commit


# Push errors that mean "someone else pushed first". Fetch and rebuild on these.
RACE_SIGNS = ("fetch first", "non-fast-forward", "incorrect old value", "cannot lock ref",
              "stale info", "already exists")


class Rejected(Exception):
    pass


# ---------- relay ----------
#
# The relay is the fast path. A post goes to the relay first, so the other side
# sees it in about a second, then to git, which is the record. Run the relay on
# Tailscale. That link is encrypted, so message text may cross it. Git alone
# still works when the relay is down.

def lan_url():
    u = os.environ.get("BRIDGE_LAN", "").strip()
    if not u:
        u = git("config", "--get", "bridge.lan", check=False).stdout.decode().strip()
    if u.lower() in ("off", "none", "0"):  # the local board has no relay
        return None
    if u and not u.startswith("http"):
        u = "http://" + u
    return u.rstrip("/") or None


def lan_call(url: str, path: str, timeout: float, body=None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url + path, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def entry_dict(e, **extra) -> dict:
    d = {"epoch": e.epoch, "state": e.state, "frm": e.frm, "to": e.to, "at": e.at, "body": e.body}
    d.update(extra)
    return d


def entry_from(d):
    """Entry from relay JSON, or None if it is malformed."""
    try:
        e = Entry(int(d["epoch"]), str(d["state"]), str(d["frm"]), str(d["to"]),
                  str(d.get("at", "")), str(d.get("body", "")))
    except (KeyError, TypeError, ValueError):
        return None
    if e.state not in STATES or not ADDR_RE.match(e.frm) or not ADDR_RE.match(e.to):
        return None
    return e


def live_send(entry, **extra) -> bool:
    url = lan_url()
    if not url:
        return False
    try:
        lan_call(url, "/post", 4, body={"entry": entry_dict(entry, **extra)})
        return True
    except Exception:
        return False


def lan_bump() -> None:
    url = lan_url()
    if url:
        try:
            lan_call(url, "/bump", 2, body={})
        except Exception:
            pass  # the relay is a speedup, never a requirement


def entry_key(e) -> tuple:
    return (e.epoch, e.state, e.frm, e.at, e.body)


class Napper:
    """Sleep between board checks. With a relay, wake at once and return live entries."""

    def __init__(self, quiet=False):
        self.url = lan_url()
        self.ver = None
        self.connect(quiet=quiet)

    def connect(self, quiet=True):
        if not self.url:
            return
        try:
            self.ver = lan_call(self.url, "/wait?since=-1", 3)["v"]
            if not quiet:
                err(f"relay {self.url} connected, posts arrive live")
        except Exception:
            self.ver = None
            if not quiet:
                err(f"relay {self.url} unreachable, polling git instead")

    def nap(self, limit: float) -> list:
        """Wait up to `limit` seconds. Return relay entries posted meanwhile."""
        limit = max(0.0, limit)
        if self.ver is not None:
            t0 = time.time()
            try:
                r = lan_call(self.url, f"/wait?since={self.ver}&timeout={limit:.0f}", limit + 8)
                self.ver = r["v"]
                return r.get("entries", [])
            except Exception:
                self.ver = None
                err("(relay dropped, polling git instead)")
                time.sleep(max(0.0, limit - (time.time() - t0)))
                return []
        time.sleep(limit)
        self.connect()
        return []


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; this picks the outbound interface
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def is_tailscale(ip: str) -> bool:
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except ValueError:
        return False
    return a == 100 and 64 <= b <= 127


def tailscale_ip():
    for exe in ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
                r"C:\Program Files\Tailscale\tailscale.exe"):
        try:
            p = subprocess.run([exe, "ip", "-4"], capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        ip = p.stdout.decode().strip().split("\n")[0].strip()
        if p.returncode == 0 and is_tailscale(ip):
            return ip
    return next((ip for ip in lan_ips() if is_tailscale(ip)), None)


RELAY_KEEP = 500          # entries the relay remembers for late waiters
RELAY_MAX_BODY = 256_000  # bytes per request


def cmd_serve(args):
    import http.server
    import threading

    cond = threading.Condition()
    start = random.randrange(1, 1 << 30)
    state = {"v": start, "log": []}  # log holds (v, entry dict)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass

        def reply(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/":
                return self.reply({"ok": True, "v": state["v"], "relay": "team bridge"})
            if u.path != "/wait":
                return self.reply({"error": "not found"}, 404)
            try:
                since = int(q.get("since", ["-1"])[0])
                t = min(60.0, max(0.0, float(q.get("timeout", ["25"])[0])))
            except ValueError:
                return self.reply({"error": "bad query"}, 400)
            with cond:
                cond.wait_for(lambda: state["v"] != since, timeout=t)
                v = state["v"]
                known = start <= since <= v  # a stale `since` from before a restart gets nothing
                ents = [e for ev, e in state["log"] if ev > since] if known else []
            self.reply({"v": v, "entries": ents})

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            if path not in ("/post", "/bump"):
                return self.reply({"error": "not found"}, 404)
            n = int(self.headers.get("Content-Length") or 0)
            if n > RELAY_MAX_BODY:
                return self.reply({"error": "too large"}, 413)
            raw = self.rfile.read(n) if n else b"{}"
            entry = None
            if path == "/post":
                try:
                    d = json.loads(raw.decode("utf-8")).get("entry")
                except (ValueError, AttributeError):
                    d = None
                e = entry_from(d) if isinstance(d, dict) else None
                if e is None:
                    return self.reply({"error": "bad entry"}, 400)
                fix = d.get("fix")
                entry = entry_dict(e, **({"fix": fix} if isinstance(fix, int) else {}))
            with cond:
                state["v"] += 1
                v = state["v"]
                if entry is not None:
                    state["log"].append((v, entry))
                    del state["log"][:-RELAY_KEEP]
                cond.notify_all()
            stamp = datetime.now().strftime("%H:%M:%S")
            if entry is not None:
                err(f"{stamp} E{entry['epoch']} {entry['state']} {entry['frm']} -> {entry['to']}")
            else:
                err(f"{stamp} ring from {self.client_address[0]}")
            self.reply({"v": v})

    host = args.host or tailscale_ip() or "0.0.0.0"
    srv = http.server.ThreadingHTTPServer((host, args.port), Handler)
    srv.daemon_threads = True
    out(f"bridge relay listening on {host}:{args.port}. Ctrl+C to stop.")
    if host == "0.0.0.0":
        err("warning: no Tailscale address found. Message text will cross the network unencrypted.")
        ips = lan_ips()
    else:
        ips = [host]
    out("Each teammate runs once (this laptop too):")
    for ip in ips or ["<this-laptop-ip>"]:
        out(f"    git config bridge.lan http://{ip}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


# ---------- parsing ----------

def escape(body: str) -> str:
    return re.sub(r"^(## E\d)", r"\\\1", body, flags=re.M)


def unescape(body: str) -> str:
    return re.sub(r"^\\(## E\d)", r"\1", body, flags=re.M)


def parse(text: str):
    entries = []
    ms = list(HEADER_RE.finditer(text))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        lines = text[m.end():end].strip("\n").split("\n")
        at = ""
        if lines and AT_RE.match(lines[0]):
            at = AT_RE.match(lines[0]).group(1)
            lines = lines[1:]
        body = unescape("\n".join(lines).strip("\n"))
        entries.append(Entry(int(m.group(1)), m.group(2), m.group(3), m.group(4), at, body))
    return entries


def first_line(body: str) -> str:
    for line in body.split("\n"):
        line = line.strip()
        if line:
            return line[:70] + ("..." if len(line) > 70 else "")
    return "(no text)"


def fold(entries):
    eps = {}
    for e in entries:
        x = eps.get(e.epoch)
        if x is None:
            if e.state not in ("READY", "FYI"):
                continue  # reply to an epoch that was never opened; ignore it
            eps[e.epoch] = Epoch(e.epoch, e.frm, e.to, e.state, first_line(e.body), [e])
            continue
        x.entries.append(e)
        if e.state in ("DONE", "ERROR", "ACK"):
            x.state = e.state
        elif e.state == "SEEN" and x.state == "READY":
            x.state = "SEEN"  # a late receipt never reopens an answered epoch
    return eps


# Routing. `me` is the reader's address. A session with a channel
# (`ann/api`) hears only posts to that channel. A reader without
# one (`ann`, the human view) hears everything sent to its person.

def split_addr(addr: str):
    p, _, c = addr.partition("/")
    return p, (c or None)


def person(addr: str) -> str:
    return addr.partition("/")[0]


def addressed(to: str, me: str) -> bool:
    tp, tc = split_addr(to)
    mp, mc = split_addr(me)
    if tp not in (mp, "all"):
        return False
    return mc is None or tc == mc


def from_self(frm: str, me: str) -> bool:
    fp, fc = split_addr(frm)
    mp, mc = split_addr(me)
    return fp == mp and (mc is None or fc == mc)


def my_turn(x: Epoch, me: str) -> bool:
    if x.state in ASKED:
        return addressed(x.to, me) and not from_self(x.opener, me)
    if x.state in ("DONE", "ERROR"):
        return from_self(x.opener, me)
    return False


def waiting_on_others(x: Epoch, me: str) -> bool:
    return x.state in ASKED and from_self(x.opener, me)


def to_me(e: Entry, me: str) -> bool:
    return addressed(e.to, me) and not from_self(e.frm, me)


def wakes(e: Entry, me: str) -> bool:
    """A post that should end a `wait`. A receipt is news, not work, so it never does."""
    return to_me(e, me) and e.state != "SEEN"


def reader_of(e: Entry, me: str) -> bool:
    """True when `me` is the exact reader a READY was sent to, so its receipt counts.

    The human view hears its agents' channels too, but only the agent on that
    channel can say the agent got the task.
    """
    if e.state != "READY":
        return False
    if e.to == "all":
        return split_addr(me)[1] is None and person(e.frm) != person(me)
    return e.to == me and e.frm != me


# ---------- identity and time ----------

def whoami(ch=None) -> str:
    """Your address: the handle, plus `/channel` when --ch or BRIDGE_CH is set."""
    me = os.environ.get("BRIDGE_ME", "").strip().lower()
    if not me:
        me = git("config", "--get", "bridge.me", check=False).stdout.decode().strip().lower()
    if not me:
        name = git("config", "--get", "user.name", check=False).stdout.decode().strip()
        me = name.split()[0].lower() if name else ""
    if not HANDLE_RE.match(me or "") or me == "all":
        raise BridgeError("no usable handle. Set one with: git config bridge.me <name>")
    ch = (ch or os.environ.get("BRIDGE_CH", "")).strip().lower()
    if ch:
        if not CHANNEL_RE.match(ch):
            raise BridgeError(f"bad channel {ch!r}. Use your track slug, like api.")
        return f"{me}/{ch}"
    return me


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def when(at: str) -> str:
    try:
        t = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return at or "?"
    secs = (datetime.now(timezone.utc) - t).total_seconds()
    if secs < 90:
        ago = "just now"
    elif secs < 5400:
        ago = f"{secs / 60:.0f}m ago"
    elif secs < 129600:
        ago = f"{secs / 3600:.0f}h ago"
    else:
        ago = f"{secs / 86400:.0f}d ago"
    return f"{t.astimezone().strftime('%a %H:%M')} ({ago})"


# ---------- rendering ----------

def render_entry(e: Entry) -> str:
    return f"## E{e.epoch} {e.state} {e.frm} -> {e.to}\nat: {e.at}\n\n{escape(e.body)}\n"


def print_entry(e: Entry, tag: str = "") -> None:
    out(f"E{e.epoch} {e.state} {e.frm} -> {e.to}  {when(e.at)}{tag}")
    for line in (e.body or "(no text)").split("\n"):
        out("    " + line)


def print_epochs(title: str, xs, me: str) -> None:
    out(f"{title} ({len(xs)})")
    if not xs:
        out("    none")
    for x in xs:
        last = x.entries[-1]
        who = f"{x.opener} -> {x.to}"
        out(f"    E{x.n:<4} {x.state:<6} {who:<44} {when(last.at):<24} {x.subject}")


# ---------- posting ----------

class NotYet(BridgeError):
    """The epoch is newer than the board. It may be live on the relay and not pushed yet."""


def post(build, retries: int = 12):
    """Build one entry from a fresh board, send it live, then record it on git.

    The relay copy goes out before the push, so the other side sees it at once.
    If a push race moves the entry to another epoch number, a fix goes out too.
    """
    at = now_utc()
    live = None       # the entry the relay delivered, if any
    tried_live = False
    races = waits = 0
    while True:
        tip = fetch()
        text = read_board(tip)
        entries = parse(text)
        try:
            entry = build(entries, fold(entries), at)
        except NotYet:
            if waits >= 5:
                raise
            waits += 1
            time.sleep(1.5)  # a live epoch is usually on git within a few seconds
            continue
        if not tried_live:
            tried_live = True
            live = entry if live_send(entry) else None
        new_text = text.rstrip("\n") + "\n\n" + render_entry(entry)
        msg = f"bridge: E{entry.epoch} {entry.state} {entry.frm} -> {entry.to}"
        try:
            sha = push_text(tip, new_text, msg)
        except Rejected:
            races += 1
            if races >= retries:
                raise BridgeError(f"gave up after {retries} push races. Try again.")
            time.sleep(0.3 + random.random() * races)
            continue
        renumbered = live is not None and live.epoch != entry.epoch
        if renumbered:
            live_send(entry, fix=live.epoch)
        else:
            lan_bump()
        how = "live, " if live is not None else ""
        out(f"posted E{entry.epoch} {entry.state} {entry.frm} -> {entry.to}  "
            f"({how}recorded {sha[:7]} on {REMOTE}/{BRANCH})")
        if renumbered:
            out(f"note: E{live.epoch} was taken by a parallel post, so this is recorded as E{entry.epoch}")
        return entry


# ---------- receipts ----------

def find_epoch(entries, d: Entry):
    """The git epoch of a delivered READY.

    A live copy can carry an epoch number that a push race changed, so match
    on sender, address, time and text instead of trusting the number.
    """
    same = [e for e in entries if e.state == "READY" and e.frm == d.frm and e.to == d.to and e.at == d.at]
    exact = [e for e in same if e.body == d.body]
    pick = exact or (same if len(same) == 1 else [])
    return pick[0].epoch if pick else None


def post_receipts(delivered, me: str, how: str, retries: int = 12):
    """Write one SEEN for each delivered READY that `me` is the reader of.

    Same order as post(). The relay copy goes first, then one git commit
    records them all. Return the receipts written.
    """
    want = [d for d in delivered if reader_of(d, me)]
    if not want:
        return []
    at = now_utc()
    sent = set()  # epochs already sent live, so a push race does not resend them
    races = waits = 0
    while True:
        tip = fetch()
        text = read_board(tip)
        entries = parse(text)
        eps = fold(entries)
        found = {}
        missing = 0
        for d in want:
            n = find_epoch(entries, d)
            if n is None:
                missing += 1
            else:
                found[n] = eps.get(n)
        if missing and waits < 5:
            waits += 1
            time.sleep(1.5)  # the READY arrived live and its push has not landed yet
            continue
        new = []
        for n, x in sorted(found.items()):
            if x is None or x.state not in ASKED:
                continue  # answered or closed already, a receipt adds nothing
            if any(e.state == "SEEN" and e.frm == me for e in x.entries):
                continue
            new.append(Entry(n, "SEEN", me, x.opener, at, f"picked up by {how}"))
        if not new:
            return []
        live_ok = True
        for e in new:
            if e.epoch not in sent:
                sent.add(e.epoch)
                live_ok = live_send(e) and live_ok
        new_text = text.rstrip("\n") + "\n\n" + "\n".join(render_entry(e) for e in new)
        if len(new) == 1:
            msg = f"bridge: E{new[0].epoch} SEEN {me} -> {new[0].to}"
        else:
            msg = f"bridge: {len(new)} receipts from {me}"
        try:
            sha = push_text(tip, new_text, msg)
        except Rejected:
            races += 1
            if races >= retries:
                raise BridgeError(f"gave up after {retries} push races")
            time.sleep(0.3 + random.random() * races)
            continue
        if not live_ok:
            lan_bump()
        for e in new:
            err(f"receipt E{e.epoch} SEEN -> {e.to} (recorded {sha[:7]})")
        return new


def send_receipts(delivered, me: str, how: str) -> None:
    """post_receipts that never raises. A lost receipt must not lose the message."""
    try:
        post_receipts(delivered, me, how)
    except Exception as ex:
        err(f"(receipt not sent: {ex})")


def await_receipt(napper, n: int, me: str, limit: float):
    """Wait up to `limit` s for the first sign that E<n> reached its reader.

    A SEEN counts, and so does a DONE or ERROR that beat it. Return the entry,
    or None.
    """
    deadline = time.time() + limit
    sign = ("SEEN", "DONE", "ERROR")
    while True:
        for d in napper.nap(min(5.0, max(0.0, deadline - time.time()))):
            e = entry_from(d)
            if e is not None and e.epoch == n and e.to == me and e.state in sign:
                return e
        try:
            _, _, eps = load()
        except BridgeError:
            eps = {}
        x = eps.get(n)
        hit = next((e for e in (x.entries if x else []) if e.state in sign), None)
        if hit is not None:
            return hit
        if time.time() >= deadline:
            return None


def read_body(args) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8").strip("\n")
    words = args.text or []
    if words == ["-"]:
        return sys.stdin.buffer.read().decode("utf-8").replace("\r\n", "\n").strip("\n")
    return " ".join(words).strip()


def need_epoch(eps, n: int) -> Epoch:
    if n not in eps:
        if n > max(eps, default=0):
            raise NotYet(f"E{n} is not on the board yet (waited about 8 s). "
                         "Run genie bridge to see open epochs.")
        raise BridgeError(f"E{n} does not exist. Run genie bridge to see open epochs.")
    return eps[n]


def check_to(to: str, me: str) -> str:
    to = to.lower()
    if person(to) == "me":  # `me/operator` is another session of the same person
        to = person(me) + to[2:]
    if not ADDR_RE.match(to):
        raise BridgeError(f"bad address {to!r}. Use a name, or name/channel like bob/api.")
    if to == me:
        raise BridgeError("you cannot post to yourself")
    return to


def cmd_ask(args):
    me = whoami(args.ch)
    to = check_to(args.who, me)
    body = read_body(args)
    if not body:
        raise BridgeError("empty task. Pass the text, - for stdin, or -f FILE.")
    if args.expect:
        body += "\n\nExpect (written before the result):\n" + args.expect.strip()

    def build(entries, eps, at):
        n = max((e.epoch for e in entries), default=0) + 1
        return Entry(n, "READY", me, to, at, body)
    napper = Napper(quiet=True) if args.confirm > 0 else None  # before the post, so no receipt slips past
    t0 = time.time()
    e = post(build)
    ch = f" --ch {split_addr(me)[1]}" if split_addr(me)[1] else ""
    if napper is not None:
        r = await_receipt(napper, e.epoch, me, args.confirm)
        if r is None:
            out(f"no receipt within {args.confirm:.0f} s. Nothing on {to} is listening, or it is busy. "
                f"The task stays open. genie bridge{ch} shows SEEN once it is picked up.")
        elif r.state == "SEEN":
            out(f"seen by {r.frm} {time.time() - t0:.1f} s after the ask ({r.body})")
        else:
            out(f"answered by {r.frm} with {r.state} {time.time() - t0:.1f} s after the ask")
    out(f"wait for the reply with: genie bridge wait --epoch {e.epoch}{ch}")


def cmd_fyi(args):
    me = whoami(args.ch)
    to = check_to(args.to, me)
    body = read_body(args)
    if not body:
        raise BridgeError("empty message")

    def build(entries, eps, at):
        n = max((e.epoch for e in entries), default=0) + 1
        return Entry(n, "FYI", me, to, at, body)
    post(build)


def cmd_reply(args):
    me = whoami(args.ch)
    state = args.cmd.upper()
    body = read_body(args)
    if state in ("DONE", "ERROR", "NOTE") and not body:
        raise BridgeError(f"{args.cmd} needs text")

    # Permission is per person, so a human can answer for any of their
    # sessions. Routing is per address, so the reply goes back to the
    # exact session that opened the epoch.
    my_ch = split_addr(me)[1]

    def other_channel(addr: str) -> bool:
        ch = split_addr(addr)[1]
        return my_ch is not None and ch is not None and ch != my_ch

    def build(entries, eps, at):
        x = need_epoch(eps, args.epoch)
        if state in ("DONE", "ERROR"):
            if x.state not in OPEN:
                raise BridgeError(f"E{x.n} is {x.state}, nothing to answer")
            if me == x.opener or person(x.to) not in (person(me), "all") or other_channel(x.to):
                raise BridgeError(f"E{x.n} was sent to {x.to}. Only the addressee writes {state}.")
            to = x.opener
        elif state == "ACK":
            if person(me) != person(x.opener) or other_channel(x.opener):
                raise BridgeError(f"only {x.opener} (the opener) can ACK E{x.n}")
            if x.state not in OPEN:
                raise BridgeError(f"E{x.n} is already {x.state}")
            to = x.to
        else:  # NOTE goes to the other side of the epoch
            mine = me == x.opener or (person(me) == person(x.opener) and person(me) != person(x.to))
            to = x.to if mine else x.opener
        return Entry(x.n, state, me, to, at, body)
    post(build)


# ---------- reading ----------

def load(timeout: float = 30):
    tip = fetch(timeout)
    entries = parse(read_board(tip))
    return tip, entries, fold(entries)


def cmd_show(args):
    me = whoami(args.ch)
    tip, entries, eps = load()
    if tip is None:
        out(f"BRIDGE is empty. Nothing on {REMOTE}/{BRANCH} yet. You are {me}.")
        out('Start it with: genie bridge ask <who> "task"')
        return
    out(f"BRIDGE  {REMOTE}/{BRANCH} @ {tip[:7]}  {len(entries)} entries  you are {me}")
    out()
    xs = sorted(eps.values(), key=lambda x: x.n)
    mine = [x for x in xs if my_turn(x, me)]
    print_epochs("Your turn", mine, me)
    print_epochs("Waiting on others", [x for x in xs if waiting_on_others(x, me)], me)
    out()
    recent = entries[-args.n:] if args.n > 0 else []
    if recent:
        out(f"Last {len(recent)} entries")
        for e in recent:
            print_entry(e)
    send_receipts([x.entries[0] for x in mine if x.state == "READY"], me, "show")


def cmd_log(args):
    tip, entries, eps = load()
    if args.epoch is not None:
        entries = [e for e in entries if e.epoch == args.epoch]
    elif args.n > 0:
        entries = entries[-args.n:]
    for e in entries:
        print_entry(e)
    if not entries:
        out("no entries")


def cmd_wait(args):
    me = whoami(args.ch)
    interval = max(5.0, args.interval)
    tip, entries, eps = load()
    if not args.new:
        pending = [x for x in eps.values() if my_turn(x, me)
                   and (args.epoch is None or x.n == args.epoch)]
        if pending:
            out("Already waiting on you:")
            for x in sorted(pending, key=lambda x: x.n):
                print_entry(x.entries[-1])
            send_receipts([x.entries[0] for x in pending if x.state == "READY"], me, "wait")
            return 0
    seen = len(entries)
    deadline = time.time() + args.timeout if args.timeout else None
    what = f"E{args.epoch}" if args.epoch is not None else "posts to you"
    err(f"waiting for {what} as {me}, checking git every {interval:.0f} s")
    napper = Napper()
    noted = set()  # receipts already reported

    def note_receipt(e):
        if e.state == "SEEN" and to_me(e, me) and entry_key(e) not in noted:
            noted.add(entry_key(e))
            err(f"E{e.epoch} seen by {e.frm} ({e.body})")

    def wanted(e):
        return wakes(e, me) and (args.epoch is None or e.epoch == args.epoch)

    while True:
        live = napper.nap(interval if deadline is None else min(interval, max(0.0, deadline - time.time())))
        hits = []
        for d in live:
            e = entry_from(d)
            if e is None:
                continue
            note_receipt(e)
            if wanted(e):
                hits.append((e, d.get("fix")))
        if hits:
            for e, fix in hits:
                print_entry(e, live_tag(fix))
            send_receipts([e for e, _ in hits], me, "wait")
            return 0
        try:
            tip, entries, eps = load()
        except BridgeError as e:
            err(f"(retrying) {e}")
            entries = None
        if entries is not None:
            for e in entries[seen:]:
                note_receipt(e)
            new = [e for e in entries[seen:] if wanted(e)]
            seen = len(entries)
            if new:
                for e in new:
                    print_entry(e)
                send_receipts(new, me, "wait")
                return 0
        if deadline is not None and time.time() >= deadline:
            err(f"timed out after {args.timeout:.0f} s")
            return 2


def cmd_watch(args):
    me = whoami(args.ch)
    interval = max(5.0, args.interval)
    tip, entries, eps = load()
    seen = len(entries)
    err(f"watching {REMOTE}/{BRANCH} as {me}, git every {interval:.0f} s. Ctrl+C to stop.")
    napper = Napper()
    shown = set()  # entries already printed from the relay, so git does not repeat them

    def show_one(e, tag=""):
        """Print one entry if it is for us. Return it when it needs a receipt."""
        if entry_key(e) in shown:
            return None
        shown.add(entry_key(e))
        if to_me(e, me) or args.all:
            if wakes(e, me):
                sys.stderr.write("\a")  # receipts print quietly
            print_entry(e, tag)
        return e if to_me(e, me) else None

    while True:
        got = []
        for d in napper.nap(interval):
            e = entry_from(d)
            if e is not None:
                got.append(show_one(e, live_tag(d.get("fix"))))
        send_receipts([e for e in got if e is not None], me, "watch")
        try:
            tip, entries, eps = load()
        except BridgeError as e:
            err(f"(retrying) {e}")
            continue
        got = [show_one(e) for e in entries[seen:]]
        seen = len(entries)
        send_receipts([e for e in got if e is not None], me, "watch")


def live_tag(fix) -> str:
    if isinstance(fix, int):
        return f"  [live, renumbered from E{fix}]"
    return "  [live]"


def cmd_whoami(args):
    out(whoami(args.ch))


def cmd_brief(args):
    try:
        me = whoami(args.ch)
        tip, entries, eps = load(timeout=6)
    except Exception as e:  # the hook must never fail a session start
        out(f"BRIDGE: could not read the board ({str(e)[:80]}). Try: genie bridge")
        return
    if tip is None:
        out('BRIDGE: empty. Post with: genie bridge ask <who> "task"')
        return
    xs = sorted(eps.values(), key=lambda x: x.n)
    mine = [x for x in xs if my_turn(x, me)]
    theirs = [x for x in xs if waiting_on_others(x, me)]
    unseen = sum(x.state == "READY" for x in theirs)
    tail = f", {unseen} not picked up yet" if unseen else ""
    if not mine:
        out(f"BRIDGE: nothing waiting on you ({me}). {len(theirs)} waiting on others{tail}.")
        return
    ch = split_addr(me)[1]
    hint = f"genie bridge --ch {ch}" if ch else "genie bridge"
    out(f"BRIDGE: {len(mine)} waiting on you ({me}). Read with: {hint}")
    for x in mine[:6]:
        last = x.entries[-1]
        out(f"  E{x.n} {x.state} {last.frm} -> {last.to}: {x.subject}")


# ---------- cli ----------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bridge", description="Team message board on its own git branch.")
    sub = ap.add_subparsers(dest="cmd")
    chan = argparse.ArgumentParser(add_help=False)
    chan.add_argument("--ch", default=None,
                      help="your channel, normally your track slug. A channel hears only its own posts")

    def body_args(p):
        p.add_argument("text", nargs="*", help='text, or "-" for stdin')
        p.add_argument("-f", "--file", help="read the text from a file")

    p = sub.add_parser("show", parents=[chan], help="what waits on you, then recent posts")
    p.add_argument("-n", type=int, default=8, help="recent entries to print")
    p = sub.add_parser("ask", parents=[chan], help="open an epoch with a task for someone")
    p.add_argument("who", help="name, name/channel (bob/api), or all")
    body_args(p)
    p.add_argument("--expect", help="what each outcome would prove, written up front")
    p.add_argument("--confirm", type=float, default=15,
                   help="seconds to wait for the reader's SEEN receipt, 0 skips (default 15)")
    p = sub.add_parser("fyi", parents=[chan], help="heads-up that needs no reply")
    body_args(p)
    p.add_argument("--to", default="all", help="name or name/channel, default all")
    for name in ("done", "error", "note", "ack"):
        p = sub.add_parser(name, parents=[chan])
        p.add_argument("epoch", type=lambda s: int(s.lstrip("Ee")))
        body_args(p)
    p = sub.add_parser("wait", parents=[chan], help="block until the other side posts to you")
    p.add_argument("--epoch", type=lambda s: int(s.lstrip("Ee")))
    p.add_argument("--interval", type=float, default=float(os.environ.get("BRIDGE_INTERVAL", 10)))
    p.add_argument("--timeout", type=float, default=0, help="seconds, 0 waits forever (exit 2 on timeout)")
    p.add_argument("--new", action="store_true", help="ignore what is already waiting on you")
    p = sub.add_parser("watch", parents=[chan], help="print posts to you as they arrive")
    p.add_argument("--interval", type=float, default=float(os.environ.get("BRIDGE_INTERVAL", 10)))
    p.add_argument("--all", action="store_true", help="print every post, not only yours")
    p = sub.add_parser("log", help="raw entries")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--epoch", type=lambda s: int(s.lstrip("Ee")))
    sub.add_parser("whoami", parents=[chan])
    sub.add_parser("brief", parents=[chan], help="one-line summary for the SessionStart hook")
    p = sub.add_parser("serve", help="relay so posts arrive live, run it on Tailscale")
    p.add_argument("--host", default=None, help="default: this machine's Tailscale IP")
    p.add_argument("--port", type=int, default=8765)

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        argv = ["show"] + argv
    args = ap.parse_args(argv)
    handlers = {
        "show": cmd_show, "ask": cmd_ask, "fyi": cmd_fyi, "done": cmd_reply,
        "error": cmd_reply, "note": cmd_reply, "ack": cmd_reply, "wait": cmd_wait,
        "watch": cmd_watch, "log": cmd_log, "whoami": cmd_whoami, "brief": cmd_brief,
        "serve": cmd_serve,
    }
    try:
        rc = handlers[args.cmd](args)
    except BridgeError as e:
        err(f"bridge: {e}")
        return 1
    except KeyboardInterrupt:
        return 130
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
