"""The repo describes itself correctly: manifests, skills, docs and no private text."""
import json
import re
import subprocess
import sys
import unittest

from helpers import GENIE, REPO, SCRIPTS

TEXT = [p for p in REPO.rglob("*") if p.is_file() and ".git" not in p.parts
        and p.suffix in (".md", ".py", ".json", ".sh", ".html", ".yml", "") and "__pycache__" not in p.parts]


def front(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    return dict(l.split(":", 1) for l in m.group(1).splitlines() if re.match(r"^[a-z-]+:", l)), text[m.end():]


class RepoTest(unittest.TestCase):
    def test_manifests(self):
        plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(market["plugins"][0]["name"], plugin["name"])
        self.assertIn(f"## {plugin['version']}", (REPO / "CHANGELOG.md").read_text())
        hooks = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]
        for groups in hooks.values():
            for h in (h for g in groups for h in g["hooks"]):
                self.assertTrue(h["command"].startswith('[ -f "$CLAUDE_PROJECT_DIR/.genie/config.json" ] || exit 0;'))

    def test_skills_and_agents(self):
        skills = sorted((REPO / "skills").glob("*/SKILL.md"))
        self.assertGreater(len(skills), 10)
        readme = (REPO / "README.md").read_text()
        for p in skills:
            fm, body = front(p)
            name = fm["name"].strip()
            self.assertEqual(name, p.parent.name)
            self.assertIn(f"/genie:{name}", readme)
            self.assertLessEqual(len(fm["description"]), 1024, name)
            self.assertLess(len(body.splitlines()), 150, name)
            for ref in re.findall(r"`(references/[\w.-]+)`", body):
                self.assertTrue((p.parent / ref).exists(), f"{name}: {ref}")
        for p in (REPO / "agents").glob("*.md"):
            fm, _ = front(p)
            self.assertEqual(fm["name"].strip(), p.stem)
            tools = {t.strip() for t in fm["tools"].split(",")}
            self.assertFalse(tools & {"Bash", "Edit", "Write", "Agent"}, f"{p.stem} is meant to be read-only")

    def test_only_people_answer_gates(self):
        for name in ("approve", "deny", "init"):
            fm, _ = front(REPO / "skills" / name / "SKILL.md")
            self.assertEqual(fm["disable-model-invocation"].strip(), "true", name)

    def test_documented_commands_exist(self):
        src = (SCRIPTS / "genie.py").read_text()
        known = set(re.findall(r'add_parser\("([\w-]+)"', src)) | set(re.findall(r'add\("([\w-]+)", "cmd_', src))
        known |= {"on", "off", "keep", "reject", "supersede", "lock", "unlock", "locks", "help"}
        known |= {"bridge", "board", "watch", "dash", "vault", "checkpoint", "second-opinion"}
        for p in TEXT:
            if p.suffix != ".md":
                continue
            for cmd in re.findall(r"`genie ([a-z][\w-]*)", p.read_text(encoding="utf-8")):
                self.assertIn(cmd, known, f"{p.name} mentions `genie {cmd}`")

    def test_doc_links_resolve(self):
        for p in TEXT:
            if p.suffix != ".md":
                continue
            for link in re.findall(r"\]\(([^)#]+)\)", p.read_text(encoding="utf-8")):
                if "://" not in link:
                    self.assertTrue((p.parent / link).exists(), f"{p.name}: {link}")

    def test_config_keys_are_documented(self):
        sys.path.insert(0, str(SCRIPTS))
        import genie_paths
        doc = (REPO / "docs" / "architecture.md").read_text()
        for key, val in genie_paths.DEFAULTS.items():
            if key != "version":
                self.assertIn(key, doc)
            if isinstance(val, dict):
                for sub in val:
                    self.assertTrue(sub in doc or sub.endswith("_minutes"), f"{key}.{sub}")

    def test_no_private_text(self):
        for p in TEXT:
            text = p.read_text(encoding="utf-8", errors="replace")
            for mail in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
                self.assertTrue(mail.endswith(("example.com", "bridge.local")), f"{p.name}: {mail}")
            for ip in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
                self.assertIn(ip, ("127.0.0.1", "0.0.0.0", "10.255.255.255"), f"{p.name}: {ip}")
            self.assertIsNone(re.search(r"(/home/|/mnt/[a-z]/|/Users/)[a-z]{3,}", text) if p.name != "test_repo.py"
                              else None, p.name)

    def test_help_runs_anywhere(self):
        p = subprocess.run([sys.executable, str(GENIE), "help"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("genie status", p.stdout)
