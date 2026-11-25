#!/bin/sh
# Walk through a whole lab in a temp project. Deletes the project at the end.
set -e
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
genie() { sh "$here/../../bin/genie" "$@"; }
say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
refused() { if "$@"; then echo "this should have been refused"; exit 1; fi; }
if python3 -c 0 2>/dev/null; then PY=python3; else PY=python; fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cp -r "$here/scorer" "$here/entries" "$tmp/"
cd "$tmp"
unset CLAUDE_PROJECT_DIR GENIE_ROOT
export GENIE_TRACK=toy GENIE_HOME="$tmp/.state"
git init -q .

say "1. Opt in, make a track, make a wish"
genie init --no-window
genie track add toy "Toy"
genie wish "make the number small" --better lower
genie preflight

say "2. Plan: fill the problem card"
genie next
"$PY" - <<'PY'
p = "tracks/toy/lab/WISH.md"
text = open(p).read().replace("TODO", "see examples/toy-lab/README.md")
open(p, "w").write(text)
p = "tracks/toy/lab/genie.toml"
text = open(p).read()
import sys
cmd = "cmd = '\"%s\" scorer/score.py {entry}'" % sys.executable.replace("\\", "/")
for tier in ("T0", "T1", "T2"):
    text = text.replace('[tiers.%s]\ncmd = ""' % tier, "[tiers.%s]\n%s" % (tier, cmd))
text = text.replace('[scorer]\npaths = []', '[scorer]\npaths = ["scorer"]')
text = text.replace('[solution]\npaths = []', '[solution]\npaths = ["entries"]')
open(p, "w").write(text)
PY

say "3. The scorer earns trust"
genie next
genie golden add entries/base.txt --score 100
genie golden
genie order entries/worse.txt entries/base.txt entries/better.txt
genie score --tier T2 --entry entries/base.txt --runs 3 --noise
genie freeze

say "4. Baseline, then ideas"
genie score --tier T2 --entry entries/base.txt --base --runs 3
genie log --tier T3 --score 104 --verdict base --evidence abc1234
genie idea add "use the better file" --test "score it" --files entries/better.txt
genie idea add "try the bad file" --test "score it" --files entries/bad.txt
genie idea add "cache it" --test "score it" --files entries/cache.txt
genie next

say "5. A win is kept, a loss cannot be"
genie idea start I1
genie score --tier T2 --entry entries/better.txt --idea I1 --runs 3
genie keep 4 --evidence def5678
genie score --tier T2 --entry entries/bad.txt --idea I2 --runs 3
refused genie keep 6 --evidence def5678
genie reject 6

say "6. A changed scorer is refused"
echo "# helpful tweak" >> scorer/score.py
refused genie score --tier T2 --entry entries/bad.txt

say "7. A gate waits for a person"
genie gate add --kind push "push def5678 so others can review it"
genie gate list

say "The ledger"
genie ledger
