#!/usr/bin/env python3
"""A read-only colleague from another model family.

    genie second-opinion ask "question"           one long-lived colleague, resumed each call
    genie second-opinion ask -f question.txt
    genie second-opinion track <slug> "question"  a fresh agent that looks at one track
    genie second-opinion last [name]              print the newest answer (colleague, or track-<slug>)

The colleague may ask for a fresh look at a track. A line in its answer of the
form `TRACK <slug>: <brief>` starts one track agent, at most 2 per answer, one
at a time. Answers land in .genie/second-opinion/, one file each.

Uses the Codex CLI with a read-only sandbox by default. Set
second_opinion.command in .genie/config.json to use another program, as a list
where {prompt}, {out} and {root} are replaced, for example
["mytool", "--read-only", "--prompt", "{prompt}", "--out", "{out}"]. A custom
program starts fresh on each call, and its own flags decide what it may touch.
"""
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genie_paths  # noqa: E402

ROOT = genie_paths.root()
CFG = genie_paths.config()["second_opinion"]
STORE = ROOT / ".genie" / "second-opinion"
MAX_TRACKS = 2

RULES = ("Rules. You are read-only: do not edit, create or delete any file, do not commit, push or submit "
         "anything. Reading and small one-off checks only, no heavy jobs. Never print tokens or keys. "
         "Answer in plain short sentences, verdict first. Give numbers with their source file. "
         "Say plainly what you could not verify.")


def run_codex(model, out, log, prompt, sid=""):
    out.unlink(missing_ok=True)
    if sid:
        cmd = ["codex", "exec", "resume", "-c", "sandbox_mode=read-only", "-o", str(out), "--json", sid, prompt]
    else:
        cmd = ["codex", "exec", "-c", f"model_reasoning_effort={CFG['effort']}", "--sandbox", "read-only",
               "-C", str(ROOT), "-o", str(out), "--json"] + (["-m", model] if model else []) + [prompt]
    if CFG["command"]:
        cmd = [a.replace("{prompt}", prompt).replace("{out}", str(out)).replace("{root}", str(ROOT))
               for a in CFG["command"]]
    with open(log, "w", encoding="utf-8") as fh:
        try:
            subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                           timeout=int(CFG["timeout"]), cwd=str(ROOT))
        except FileNotFoundError:
            fh.write(f"{cmd[0]} is not on PATH\n")
        except subprocess.TimeoutExpired:
            fh.write(f"\ntimed out after {CFG['timeout']} s\n")
    return out.exists() and out.stat().st_size > 0  # the answer file decides, not the exit code


def stamp():
    return time.strftime("%m%d-%H%M%S")


def ask_track(slug, question):
    if not (ROOT / "tracks" / slug).is_dir():
        print(f"track {slug}: no such track", file=sys.stderr)
        return False
    out, log = STORE / f"track-{slug}-{stamp()}.md", STORE / f"track-{slug}-{stamp()}.log"
    notes = genie_paths.config()["notes"]
    prompt = (f"You give a second opinion on one track of the project at {ROOT}. Track folder: tracks/{slug}/. "
              f"Read its README.md, STATE.md and SCORES.md where they exist, the lab folder if there is one, and "
              f"the notes in {notes}/findings/ about {slug}. The track's own agent may be stuck in its own framing. "
              "Your job is a fresh view: what would you do differently, what is the single largest gain still open, "
              f"and what is the cheapest test of it. Rank at most 3 ideas. {RULES}\n\nQuestion: {question}")
    ok = run_codex(CFG["track_model"] or CFG["model"], out, log, prompt)
    print(f"track {slug}: {out}" if ok else f"track {slug}: FAILED, see {log}")
    return ok


def ask(question):
    out, log = STORE / f"colleague-{stamp()}.md", STORE / f"colleague-{stamp()}.log"
    sess = STORE / "session"
    sid = sess.read_text(encoding="utf-8").strip() if sess.exists() and not CFG["command"] else ""
    prompt = question
    if not sid:
        cfg = genie_paths.config()
        prompt = (f"You are the colleague of the overseer of a fleet of agents. The project at {ROOT} holds its "
                  f"memory. Start with {cfg['notes']}/{cfg['hub']}.md, {cfg['notes']}/decisions.md and "
                  "tracks/<slug>/STATE.md for each track. The overseer judges plans and asks you when it wants a "
                  "second opinion, so disagree where the evidence allows, and say what you would do instead. Rank "
                  "ideas by the size of the gain. You may ask for a fresh look at one track: end your answer with "
                  f"a line 'TRACK <slug>: <what to look at>' for each, at most {MAX_TRACKS}. Earlier answers are in "
                  f"{STORE}. {RULES}\n\nFirst question: {question}")
    if not run_codex(CFG["model"], out, log, prompt, sid):
        print(f"colleague: FAILED, see {log}")
        return False
    if not sid and not CFG["command"]:
        m = re.search(r'"thread_id":\s*"([^"]+)"', log.read_text(encoding="utf-8", errors="replace"))
        if m:
            sess.write_text(m.group(1), encoding="utf-8")
    print(f"colleague: {out}")
    hits = re.findall(r"^TRACK ([a-z0-9-]+): (.+)$", out.read_text(encoding="utf-8", errors="replace"), re.M)
    for slug, brief in hits[:MAX_TRACKS]:
        ask_track(slug, brief)
    return True


def words(args):
    if args[:1] == ["-f"] and len(args) > 1:
        return Path(args[1]).read_text(encoding="utf-8")
    return " ".join(args).strip()


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not genie_paths.enabled():
        print("This project has not opted in. Run `genie init`.")
        return 1
    STORE.mkdir(parents=True, exist_ok=True)
    if a[:1] == ["ask"] and words(a[1:]):
        return 0 if ask(words(a[1:])) else 1
    if a[:1] == ["track"] and len(a) > 2 and words(a[2:]):
        return 0 if ask_track(a[1], words(a[2:])) else 1
    if a[:1] == ["last"]:
        hits = sorted(STORE.glob(f"{a[1] if len(a) > 1 else 'colleague'}-*.md"), key=lambda p: p.stat().st_mtime)
        print(hits[-1].read_text(encoding="utf-8") if hits else "no answer yet")
        return 0
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
