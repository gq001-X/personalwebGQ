"""Conservative binding and isolated history persistence acceptance checks."""
from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
import core
import history_store


class CoreEdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="repolens-edge-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def source(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def test_redefined_functions_have_unique_ids_and_ambiguous_call(self):
        self.source("main.py", "def target():\n    return 1\ndef target():\n    return 2\ndef run():\n    return target()\n")
        report = core.analyze_repository(self.root)
        identifiers = [symbol["id"] for symbol in report["symbols"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertFalse(next(call for call in report["calls"] if call["expression"] == "target")["resolved"])

    def test_nested_function_resolves_nearest_lexical_scope(self):
        self.source("main.py", "def helper():\n    return 0\ndef outer():\n    def helper():\n        return 1\n    return helper()\n")
        report = core.analyze_repository(self.root)
        call = next(call for call in report["calls"] if call["expression"] == "helper")
        self.assertEqual(call["callee"], "main.py::outer.helper")

    def test_conditional_import_is_not_a_certain_target(self):
        self.source("helper.py", "def finish():\n    return 1\n")
        self.source("main.py", "if ENABLED:\n    from helper import finish\ndef run():\n    return finish()\n")
        report = core.analyze_repository(self.root)
        call = next(call for call in report["calls"] if call["expression"] == "finish")
        self.assertFalse(call["resolved"])

    def test_star_import_may_override_preexisting_binding(self):
        self.source("helper.py", "def target():\n    return 2\n")
        self.source("main.py", "def target():\n    return 1\nfrom helper import *\ndef run():\n    return target()\n")
        report = core.analyze_repository(self.root)
        self.assertFalse(next(call for call in report["calls"] if call["expression"] == "target")["resolved"])

    def test_custom_eval_and_local_pickle_are_not_library_findings(self):
        self.source("pickle.py", "def loads(value):\n    return value\n")
        self.source("main.py", "from pickle import loads\ndef eval(value):\n    return value\ndef run(value):\n    return eval(loads(value))\n")
        self.assertEqual(core.analyze_repository(self.root)["findings"], [])

    def test_snapshot_hash_uses_original_crlf_bytes(self):
        raw = b"def run(value):\r\n    return eval(value)\r\n"
        (self.root / "main.py").write_bytes(raw)
        report = core.analyze_repository(self.root)
        file = report["files"][0]
        self.assertEqual(file["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertNotIn("\r", file["content"])
        self.assertIn(report["findings"][0]["snippet"], file["content"])

    def test_unreadable_file_does_not_abort_other_files(self):
        self.source("bad.py", "def nope():\n    pass\n")
        self.source("good.py", "def works():\n    return 1\n")
        actual_open = Path.open

        def deny_one(path, *args, **kwargs):
            if path.name == "bad.py":
                raise PermissionError("simulated unreadable file")
            return actual_open(path, *args, **kwargs)

        with patch.object(Path, "open", deny_one):
            report = core.analyze_repository(self.root)
        self.assertIn("good.py::works", {s["id"] for s in report["symbols"]})
        self.assertTrue(any(item["path"] == "bad.py" for item in report["skipped"]))


class HistoryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="repolens-history-test-")
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "isolated.sqlite3"
        override = patch.object(history_store, "DB_PATH", self.db_path)
        override.start()
        self.addCleanup(override.stop)
        self.report = core.analyze_repository(APP_ROOT / "demo_repo")

    def test_scan_and_messages_round_trip_without_touching_demo_history(self):
        key = history_store.save_scan(self.report)
        messages = [{"role": "user", "content": "检查调用关系"}, {"role": "assistant", "content": "引用 main.py:6"}]
        history_store.save_messages(key, messages)
        self.assertEqual(history_store.load_scan(key), self.report)
        self.assertEqual(history_store.load_messages(key), messages)
        scans = history_store.list_scans()
        self.assertEqual(len(scans), 1)
        self.assertEqual(scans[0]["key"], key)
        self.assertIsNone(history_store.load_scan("missing"))
        self.assertEqual(history_store.load_messages("missing"), [])

    def test_same_snapshot_rescan_resets_conversation_and_limits_message_count(self):
        key = history_store.save_scan(self.report)
        history_store.save_messages(key, [{"role": "user", "content": str(i)} for i in range(50)])
        self.assertEqual(len(history_store.load_messages(key)), 40)
        self.assertEqual(history_store.load_messages(key)[0]["content"], "10")
        self.assertEqual(history_store.save_scan(self.report), key)
        self.assertEqual(history_store.load_messages(key), [])

    def test_history_retains_only_ten_newest_scans(self):
        for i in range(12):
            report = copy.deepcopy(self.report)
            report["project"]["scan_id"] = f"snapshot-{i}"
            report["project"]["scanned_at"] = f"2026-10-27T00:{i:02d}:00+00:00"
            history_store.save_scan(report)
        scans = history_store.list_scans()
        self.assertEqual(len(scans), 10)
        self.assertEqual(scans[0]["scan_id"], "snapshot-11")
        self.assertNotIn("snapshot-0", {scan["scan_id"] for scan in scans})


if __name__ == "__main__":
    unittest.main()
