"""Tests run against a throwaway Claude directory filled with fictional notes."""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claude_recall as cr  # noqa: E402


def write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.cd = self.home / ".claude"
        self.projects = self.home / "code"
        self.env = {k: os.environ.get(k) for k in
                    ("HOME", "RECALL_CLAUDE_DIR", "CLAUDE_CONFIG_DIR", "RECALL_CONFIG",
                     "RECALL_DB", "RECALL_LOG", "RECALL_PROJECT_ROOTS")}
        for k in self.env:
            os.environ.pop(k, None)
        os.environ["HOME"] = str(self.home)
        os.environ["RECALL_CLAUDE_DIR"] = str(self.cd)
        os.environ["RECALL_PROJECT_ROOTS"] = str(self.projects)

        write(self.cd / "CLAUDE.md", "# Global rules\n\n## Browser\nOpen local HTML with the helper script only.\n")
        write(self.cd / "refs" / "browser.md",
              "# Browser notes\n\n## Modal dialog\nA bad path makes the OS show a modal dialog and the command hangs.\n")
        write(self.cd / "decisions" / "0007-use-wrapper.md",
              "# 0007: Wrap the open command\n\n- date: 2026-01-15\n\nThe shell froze twice, so we wrapped it.\n")
        write(self.cd / "lessons" / "2026-02-01-frozen-terminal.md",
              "---\ndate: 2026-02-01\nkind: failure\ntags: [hang, freeze, stuck, unresponsive]\n---\n"
              "# Terminal stuck after opening a file\n\n## Cause\nA dialog waited for input.\n")
        write(self.cd / "lessons" / "_template.md", "---\ndate: YYYY-MM-DD\nkind: failure | success\n"
              "project: global\n---\n# title\n")
        write(self.cd / "skills" / "release" / "SKILL.md",
              "---\nname: release\ndescription: Cut a release and tag it\n---\n# Release\nBump the version.\n")
        mem = self.cd / "projects" / (cr.encode_path(str(self.projects)) + "-todo-app") / "memory"
        write(mem / "db.md", "# Database\nThe todo app uses SQLite with WAL mode.\n")
        write(mem.parent / "session-1.jsonl",
              json.dumps({"type": "user", "text": "why does the build hang on CI"}) + "\n"
              + json.dumps({"type": "assistant", "text": "the build hangs because of a prompt"}) + "\n")
        write(mem.parent / "subagents" / "agent-1.jsonl", json.dumps({"text": "build hang"}) + "\n")
        write(self.projects / "todo-app" / "CLAUDE.md", "# Todo app\n\nRun tests with make test.\n")
        write(self.projects / "todo-app" / "AGENTS.md", "# Agents\n\nNever commit generated files.\n")
        write(self.projects / "todo-app" / "notes" / "perf.md", "# Performance\nPagination fixed the slow list.\n")
        write(self.projects / "todo-app" / "README.md", "# Not indexed\nunique-readme-token\n")
        write(self.projects / "todo-app" / "node_modules" / "x" / "CLAUDE.md", "# vendored\nvendored-token\n")
        write(self.projects / "todo-app" / ".claude" / "skills" / "deploy" / "SKILL.md",
              "---\nname: deploy\n---\n# Deploy\nShip to staging first.\n")

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def cfg(self):
        return cr.Config()

    def run_cli(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cr.main(list(args))
        return buf.getvalue()

    def paths(self, results):
        return [Path(f["best"]["path"]).name for f in results]


class TestSources(Fixture):
    def test_collects_expected_files(self):
        found = {(p.name, kind, proj) for p, kind, proj in cr.sources(self.cfg())}
        self.assertIn(("CLAUDE.md", "context", "global"), found)
        self.assertIn(("browser.md", "refs", "global"), found)
        self.assertIn(("0007-use-wrapper.md", "adr", "global"), found)
        self.assertIn(("2026-02-01-frozen-terminal.md", "lesson", "global"), found)
        self.assertIn(("SKILL.md", "skill", "global"), found)
        self.assertIn(("db.md", "memory", "todo-app"), found)
        self.assertIn(("CLAUDE.md", "project", "todo-app"), found)
        self.assertIn(("AGENTS.md", "project", "todo-app"), found)
        self.assertIn(("perf.md", "project", "todo-app"), found)
        self.assertIn(("SKILL.md", "skill", "todo-app"), found)

    def test_skips_templates_readmes_and_vendored(self):
        names = [str(p) for p, _, _ in cr.sources(self.cfg())]
        self.assertFalse(any(n.endswith("_template.md") for n in names))
        self.assertFalse(any(n.endswith("README.md") for n in names))
        self.assertFalse(any("node_modules" in n for n in names))

    def test_no_project_roots_by_default(self):
        os.environ.pop("RECALL_PROJECT_ROOTS")
        kinds = {kind for _, kind, _ in cr.sources(self.cfg())}
        projects = {proj for _, kind, proj in cr.sources(self.cfg()) if kind == "project"}
        self.assertNotIn("project", kinds)
        self.assertEqual(projects, set())

    def test_config_file_collections(self):
        write(self.cd / "playbooks" / "oncall.md", "# On call\nPage the owner.\n")
        write(self.cd / "recall.json", json.dumps({"collections": [
            {"path": "playbooks", "kind": "playbook", "weight": 5}]}))
        cfg = self.cfg()
        found = {(p.name, kind) for p, kind, _ in cr.sources(cfg)}
        self.assertIn(("oncall.md", "playbook"), found)
        self.assertNotIn(("browser.md", "refs"), found)  # replaced the defaults
        self.assertEqual(cfg.weight["playbook"], 5)


class TestSearch(Fixture):
    def setUp(self):
        super().setUp()
        self.c = self.cfg()
        self.con = cr.connect(self.c)
        self.total, self.changed, _ = cr.sync(self.con, self.c)

    def test_index_counts(self):
        self.assertEqual(self.total, self.changed)
        self.assertEqual(self.total, len(cr.sources(self.c)))
        self.assertEqual(cr.sync(self.con, self.c)[1], 0)  # second run: nothing changed

    def test_synonyms_are_or_and_rank_by_distinct_terms(self):
        res = cr.search(self.con, self.c, ["modal", "dialog", "hangs"])
        self.assertEqual(self.paths(res)[0], "browser.md")  # matches all three
        self.assertIn("2026-02-01-frozen-terminal.md", self.paths(res))  # matches "dialog" only

    def test_tags_catch_other_wording(self):
        res = cr.search(self.con, self.c, ["freeze"])
        self.assertIn("2026-02-01-frozen-terminal.md", self.paths(res))

    def test_kind_and_project_filters(self):
        res = cr.search(self.con, self.c, ["dialog"], kinds={"lesson"})
        self.assertEqual(self.paths(res), ["2026-02-01-frozen-terminal.md"])
        res = cr.search(self.con, self.c, ["sqlite", "pagination"], project="todo")
        self.assertEqual(sorted(self.paths(res)), ["db.md", "perf.md"])

    def test_short_terms_fall_back_to_like(self):
        res = cr.search(self.con, self.c, ["OS"])
        self.assertIn("browser.md", self.paths(res))

    def test_quotes_in_terms_are_safe(self):
        self.assertEqual(cr.search(self.con, self.c, ['"); DROP TABLE files; --']), [])

    def test_incremental_update_and_removal(self):
        p = self.cd / "refs" / "browser.md"
        time.sleep(0.01)
        write(p, "# Browser notes\nnow mentions kaleidoscope\n")
        os.utime(p, (time.time() + 5, time.time() + 5))
        (self.cd / "decisions" / "0007-use-wrapper.md").unlink()
        _, changed, gone = cr.sync(self.con, self.c)
        self.assertEqual((changed, gone), (1, 1))
        self.assertEqual(self.paths(cr.search(self.con, self.c, ["kaleidoscope"])), ["browser.md"])
        self.assertEqual(cr.search(self.con, self.c, ["froze"]), [])

    def test_headings_in_code_fences_are_not_split(self):
        chunks = cr.split_chunks("# A\ntext\n```\n# not a heading\n```\n## B\nmore\n")
        self.assertEqual([h for h, _, _ in chunks], ["A", "B"])
        self.assertEqual(chunks[1][1], 6)


class TestCli(Fixture):
    def test_search_output_and_log(self):
        out = self.run_cli("modal", "freeze", "--snippet")
        self.assertIn("browser.md", out)
        self.assertIn("> A bad path", out)
        entries = cr.read_log(self.cfg())
        self.assertEqual(entries[-1]["terms"], ["modal", "freeze"])

    def test_no_match_message(self):
        self.assertIn("No match", self.run_cli("zzzqqq-nothing"))

    def test_mark_and_stats(self):
        self.run_cli("zzzqqq-nothing")
        self.run_cli("--mark", "miss", "--note", "looked for X")
        self.run_cli("dialog")
        self.run_cli("--mark", "hit")
        s = cr.stats(self.cfg())
        self.assertEqual(s["searches"], 2)
        self.assertEqual(len(s["zero"]), 1)
        self.assertEqual(s["hit"], 1)
        self.assertEqual(s["miss"][0]["terms"], ["zzzqqq-nothing"])
        self.assertIn("2 searches", self.run_cli("--stats"))

    def test_logs_skip_subagents(self):
        out = self.run_cli("--logs", "build hang")
        self.assertIn("1 transcripts matched", out)
        self.assertIn("session-1.jsonl", out)
        self.assertIn("todo-app", out)
        self.assertNotIn("agent-1.jsonl", out)

    def test_logs_python_fallback(self):
        base = self.cd / "projects"
        old = cr.shutil.which
        cr.shutil.which = lambda name: None
        try:
            rows = cr.grep_logs(base, ["build hang"])
        finally:
            cr.shutil.which = old
        self.assertEqual([Path(p).name for _, _, p in rows], ["session-1.jsonl"])

    def test_new_lesson_uses_template(self):
        out = self.run_cli("--new", "success", "cache-warmup", "--project", "todo-app").strip()
        text = Path(out).read_text(encoding="utf-8")
        self.assertIn("kind: success", text)
        self.assertIn("project: todo-app", text)
        self.assertNotIn("YYYY-MM-DD", text)

    def test_new_lesson_default_template_and_slug_check(self):
        (self.cd / "lessons" / "_template.md").unlink()
        out = self.run_cli("--new", "failure", "x").strip()
        self.assertIn("## Next time", Path(out).read_text(encoding="utf-8"))
        with self.assertRaises(SystemExit):
            self.run_cli("--new", "failure", "../escape")

    def test_sources_and_custom_db_location(self):
        db = Path(self.tmp.name) / "elsewhere" / "idx.db"
        os.environ["RECALL_DB"] = str(db)
        try:
            self.run_cli("dialog")
        finally:
            os.environ.pop("RECALL_DB")
        self.assertTrue(db.exists())
        self.assertIn("files. Claude dir:", self.run_cli("--sources"))


if __name__ == "__main__":
    unittest.main()
