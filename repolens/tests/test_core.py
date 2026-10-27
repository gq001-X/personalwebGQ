"""Meaningful acceptance checks for the read-only analysis and report evidence."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from core import analyze_repository, report_markdown


class RepositoryAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="repolens-test-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def source(self, relative: str, content: str) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def test_clean_repository_has_no_findings(self):
        self.source("main.py", "from helper import add\n\ndef run():\n    return add(2, 3)\n")
        self.source("helper.py", "def add(a, b):\n    return a + b\n")
        report = analyze_repository(self.root)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["project"]["python_files"], 2)
        self.assertEqual({f["path"] for f in report["files"]}, {"main.py", "helper.py"})

    def test_demo_finds_five_intentional_risks_with_verified_evidence(self):
        report = analyze_repository(APP_ROOT / "demo_repo")
        self.assertEqual(len(report["findings"]), 5)
        self.assertEqual(report["project"]["python_files"], 5)
        file_map = {f["path"]: f for f in report["files"]}
        for finding in report["findings"]:
            with self.subTest(finding=finding["id"]):
                snapshot = file_map[finding["file"]]
                self.assertTrue(finding["evidence_verified"])
                self.assertGreaterEqual(finding["line"], 1)
                self.assertGreaterEqual(finding["end_line"], finding["line"])
                lines = snapshot["content"].splitlines()
                self.assertLessEqual(finding["end_line"], len(lines))
                self.assertIn(finding["snippet"].strip(), snapshot["content"])
                for key in ("trigger", "impact", "recommendation", "description"):
                    self.assertTrue(finding[key].strip(), key)
        self.assertEqual(
            {finding["file"] for finding in report["findings"]},
            {"pricing.py", "storage.py", "integrations.py", "service.py"},
        )

    def test_snapshot_hash_and_evidence_do_not_read_later_edits(self):
        initial = "def calculate(value):\n    return eval(value)\n"
        path = self.source("sample.py", initial)
        raw_snapshot = path.read_bytes()
        report = analyze_repository(self.root)
        snapshot = next(f for f in report["files"] if f["path"] == "sample.py")
        self.assertEqual(snapshot["sha256"], hashlib.sha256(raw_snapshot).hexdigest())
        path.write_text("# source changed after scan\n", encoding="utf-8")
        self.assertEqual(snapshot["content"].replace("\r\n", "\n"), initial)
        markdown = report_markdown(report)
        self.assertIn("sample.py", markdown)
        self.assertIn("eval(value)", markdown)

    def test_sensitive_files_and_virtual_environments_are_ignored(self):
        self.source("safe.py", "def identity(value):\n    return value\n")
        self.source(".env", "API_KEY=DEMO-SENSITIVE-CONTENT\n")
        self.source(".venv/lib/danger.py", "eval(user_input)\n")
        self.source("venv/lib/danger.py", "eval(user_input)\n")
        self.source("node_modules/package/danger.py", "eval(user_input)\n")
        self.source(".git/hooks/danger.py", "eval(user_input)\n")
        report = analyze_repository(self.root)
        self.assertEqual(report["findings"], [])
        self.assertEqual({f["path"] for f in report["files"]}, {"safe.py"})
        self.assertNotIn("DEMO-SENSITIVE-CONTENT", report_markdown(report))

    def test_cross_file_direct_calls_are_resolved(self):
        self.source("main.py", "from service import process\n\ndef run():\n    return process()\n")
        self.source("service.py", "from helper import finish\n\ndef process():\n    return finish()\n")
        self.source("helper.py", "def finish():\n    return 42\n")
        report = analyze_repository(self.root)
        edges = {(c["caller"], c["callee"]) for c in report["calls"] if c["resolved"]}
        self.assertIn(("main.py::run", "service.py::process"), edges)
        self.assertIn(("service.py::process", "helper.py::finish"), edges)

    def test_dynamic_calls_are_not_assigned_to_same_named_function(self):
        self.source("main.py", "def run(plugin):\n    return plugin.handle()\n")
        self.source("helper.py", "def handle():\n    return 42\n")
        report = analyze_repository(self.root)
        dynamic = [c for c in report["calls"] if c["expression"] == "plugin.handle"]
        self.assertTrue(dynamic, "The unresolved dynamic call must remain visible.")
        self.assertTrue(all(not c["resolved"] for c in dynamic))
        self.assertTrue(all(c["callee"] is None for c in dynamic))

    def test_library_aliases_retain_detection(self):
        self.source(
            "aliases.py",
            "import pickle as serializer\nimport subprocess as sp\n"
            "def decode(value):\n    return serializer.loads(value)\n"
            "def command(value):\n    return sp.run(value, shell=True)\n",
        )
        report = analyze_repository(self.root)
        self.assertEqual(len(report["findings"]), 2)

    def test_safe_library_options_are_not_flagged(self):
        self.source(
            "safe_options.py",
            "import ast\nimport json\nimport subprocess\nimport requests\n"
            "def decode(value):\n    return ast.literal_eval(value)\n"
            "def command(value):\n    return subprocess.run(['echo', value], shell=False)\n"
            "def request(url):\n    return requests.get(url, verify=True, timeout=5)\n",
        )
        self.assertEqual(analyze_repository(self.root)["findings"], [])

    def test_missing_or_file_root_is_rejected(self):
        for root in (self.root / "missing", self.source("one.py", "pass\n")):
            with self.subTest(root=str(root)):
                with self.assertRaises(ValueError):
                    analyze_repository(root)

    def test_scanning_never_imports_or_executes_target_code(self):
        marker = self.root / "must-not-exist.txt"
        self.source(
            "side_effect.py",
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
        )
        before = (self.root / "side_effect.py").read_bytes()
        analyze_repository(self.root)
        self.assertFalse(marker.exists())
        self.assertEqual((self.root / "side_effect.py").read_bytes(), before)

    def test_syntax_error_is_reported_without_losing_other_files(self):
        self.source("broken.py", "def broken(:\n    pass\n")
        self.source("working.py", "def answer():\n    return 42\n")
        report = analyze_repository(self.root)
        self.assertTrue(report["warnings"])
        self.assertIn("working.py::answer", {symbol["id"] for symbol in report["symbols"]})

    def test_external_symlink_is_never_scanned(self):
        self.source("safe.py", "answer = 42\n")
        with tempfile.TemporaryDirectory(prefix="repolens-outside-") as outside:
            secret = Path(outside) / "outside.py"
            secret.write_text("eval(user_input)\n", encoding="utf-8")
            try:
                (self.root / "linked.py").symlink_to(secret)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"This environment cannot create symlinks: {exc}")
            report = analyze_repository(self.root)
            self.assertEqual(report["findings"], [])
            self.assertNotIn("linked.py", {f["path"] for f in report["files"]})


if __name__ == "__main__":
    unittest.main()
