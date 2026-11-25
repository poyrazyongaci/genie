#!/usr/bin/env python3
"""Live dashboard on localhost. No model calls, files and git only.

    genie dash            # http://localhost:8799/

Sources, read again on each refresh (cached for a few seconds):
  status board     the hub note
  open requests    the local board and the bridge branch as of the last fetch
  gates            every gate queue
  sessions, labs   genie status
"""
import json
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import genie_paths  # noqa: E402
import genie_lab  # noqa: E402

ROOT = genie_paths.root()
PAGE = SCRIPTS / "dash.html"
TTL = 20          # seconds a snapshot is reused
GENIE_TTL = 60
FEED = 12


def run(cmd, timeout=40):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return p.stdout
    except Exception:
        return ""


def board_text(where):
    if where == "board":
        return run(["git", "--git-dir", str(ROOT / ".genie" / "board.git"), "show", "genie-bridge:BRIDGE.md"])
    cfg = genie_paths.config()["bridge"]
    return run(["git", "show", f"{cfg['remote']}/{cfg['branch']}:BRIDGE.md"])


def plain(cell):
    cell = re.sub(r"\[\[[^\]|\\]*\\?\|([^\]]*)\]\]", r"\1", cell)
    cell = re.sub(r"\[\[([^\]]*)\]\]", r"\1", cell)
    return cell.replace("`", "").strip()


def status_board():
    path = genie_paths.hub_note(ROOT)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [], 0
    m = re.search(r"^## Status board\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    rows = []
    for line in (m.group(1) if m else "").split("\n"):
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [plain(c) for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) >= 5 and cells[0] != "Track":
            rows.append(dict(track=cells[0], owner=cells[1], local=cells[2], outside=cells[3], next=cells[4]))
    return rows, path.stat().st_mtime


def board_view(where):
    import bridge  # the parser only, nothing is posted
    entries = bridge.parse(board_text(where))
    open_rows = []
    for n, x in sorted(bridge.fold(entries).items()):
        if x.state not in ("READY", "SEEN"):
            continue
        notes = [e for e in x.entries if e.state == "NOTE"]
        open_rows.append(dict(where=where, n=n, state=x.state, frm=x.opener, to=x.to, at=x.entries[0].at,
                              subject=x.entries[0].body.strip()[:260],
                              latest=(notes[-1].body.strip()[:260] if notes else "")))
    feed = [dict(n=e.epoch, state=e.state, frm=e.frm, to=e.to, at=e.at, body=e.body.strip()[:900])
            for e in entries if e.state in ("DONE", "ERROR", "FYI") and e.body.strip()]
    return open_rows, feed


def gate_rows():
    out = []
    for home in genie_lab.gate_homes():
        for g in genie_lab.gates(home)[1]:
            if g["status"] == "open":
                out.append(dict(id=g["id"], kind=g["kind"], scope=g["scope"], what=g["what"], by=g["asked by"]))
    return out


def genie_tables():
    text = run([sys.executable, str(SCRIPTS / "genie.py"), "status"], timeout=60)
    lines = text.split("\n")
    out = {}
    i = 0
    while i < len(lines):
        name = lines[i].strip()
        if name in ("SESSIONS", "TRACKS", "LABS") and i + 2 < len(lines):
            spans = [(m.start(), m.end()) for m in re.finditer(r"-+", lines[i + 2])]
            head = [lines[i + 1][a:b].strip() for a, b in spans]
            rows = []
            i += 3
            while i < len(lines) and lines[i].strip():
                row = lines[i]
                cells = [row[a:(spans[k + 1][0] if k + 1 < len(spans) else len(row))].strip()
                         for k, (a, _b) in enumerate(spans)]
                rows.append(cells)
                i += 1
            out[name.lower()] = dict(head=head, rows=rows)
            continue
        i += 1
    return out


_cache = {"at": 0.0, "data": None, "genie_at": 0.0, "genie": {}}


def snapshot():
    now = time.time()
    if _cache["data"] and now - _cache["at"] < TTL:
        return _cache["data"]
    if now - _cache["genie_at"] >= GENIE_TTL:
        _cache["genie"] = genie_tables()
        _cache["genie_at"] = now
    rows, mtime = status_board()
    opened, feed = [], []
    for where in ("board", "bridge"):
        o, f = board_view(where)
        opened += o
        feed += f
    feed.sort(key=lambda f: f["at"], reverse=True)
    cfg = genie_paths.config()["dash"]
    data = dict(now=now, board_mtime=mtime, status=rows, gates=gate_rows(), open=opened, feed=feed[:FEED],
                title=cfg["title"] or f"{ROOT.name}, fleet board", deadline=cfg["deadline"], genie=_cache["genie"])
    _cache.update(at=now, data=data)
    return data


class Handler(BaseHTTPRequestHandler):
    def send(self, code, kind, body):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/data.json":
            try:
                body = json.dumps(snapshot()).encode()
                self.send(200, "application/json", body)
            except Exception as e:  # the page shows the error and keeps the old data
                self.send(500, "application/json", json.dumps({"error": str(e)}).encode())
        elif path in ("/", "/index.html"):
            self.send(200, "text/html; charset=utf-8", PAGE.read_bytes())
        else:
            self.send(404, "text/plain", b"not found")

    def log_message(self, *_args):
        pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not genie_paths.enabled():
        print("This project has not opted in. Run `genie init`.")
        return 1
    port = int(argv[0]) if argv else int(genie_paths.config()["dash"]["port"])
    print(f"dashboard on http://localhost:{port}/", flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
