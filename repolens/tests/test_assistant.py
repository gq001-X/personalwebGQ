"""Offline and mocked-model checks; no API keys or network requests are used."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

import assistant_service as service
from core import analyze_repository


class AssistantEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = analyze_repository(APP_ROOT / "demo_repo")
        cls.config = {
            "configured": True,
            "enabled": True,
            "api_key": "TEST-ONLY-NOT-A-REAL-KEY",
            "base_url": "https://model.example.invalid/v1",
            "model": "test-model",
        }

    def citation(self, line=1, end_line=None, file="pricing.py"):
        return {"file": file, "line": line, "end_line": line if end_line is None else end_line}

    def tool_message(self, name="read_source", args=None):
        if args is None:
            args = {"file": "pricing.py", "line": 1, "end_line": 8}
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "test-call-1",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }],
        }

    def final_message(self, citations=None):
        if citations is None:
            citations = [self.citation(6)]
        return {"role": "assistant", "content": json.dumps({
            "answer": "pricing.py:6 使用动态求值，需要确认输入信任边界。",
            "citations": citations,
        }, ensure_ascii=False)}

    def model_answer(self, responses):
        with patch.object(service, "load_model_config", return_value=self.config):
            with patch.object(service, "_request_completion", side_effect=responses) as request:
                result = service.answer_question(self.report, "解释 pricing.py 的风险", use_model=True)
        return result, request

    def test_offline_structure_risk_and_calls_have_real_citations(self):
        questions = ("解释 pricing.py", "哪些问题应该优先修复？", "项目有哪些调用关系？")
        with patch.object(service, "_request_completion") as request:
            for question in questions:
                with self.subTest(question=question):
                    result = service.answer_question(self.report, question)
                    self.assertEqual(result["mode"], "本地证据问答")
                    self.assertTrue(result["answer"].strip())
                    self.assertTrue(result["citations"])
                    self.assertEqual(
                        result["citations"],
                        service.validate_citations(self.report, result["citations"]),
                    )
            request.assert_not_called()

    def test_read_tool_rejects_paths_outside_snapshot(self):
        for path in ("../.env", "../../core.py", "C:/Windows/system.ini", "/etc/passwd", "missing.py"):
            with self.subTest(path=path):
                result = service.execute_tool(self.report, "read_source", {"file": path, "line": 1, "end_line": 5})
                self.assertIn("error", result)
                self.assertNotIn("lines", result)

    def test_read_tool_returns_only_requested_snapshot_lines(self):
        result = service.execute_tool(self.report, "read_source", {"file": "pricing.py", "line": 4, "end_line": 7})
        self.assertEqual(result["file"], "pricing.py")
        self.assertEqual([line["line"] for line in result["lines"]], [4, 5, 6, 7])
        source = next(f for f in self.report["files"] if f["path"] == "pricing.py")["content"].splitlines()
        self.assertEqual([line["text"] for line in result["lines"]], source[3:7])

    def test_citation_validation_rejects_invalid_ranges_paths_and_types(self):
        good = self.citation(4, 7)
        invalid = [
            self.citation(file="../pricing.py"), self.citation(file="missing.py"),
            self.citation(0), self.citation(-1), self.citation(99999),
            self.citation(7, 4), self.citation(4, 99999),
            self.citation("4"), self.citation(4.0), self.citation(True),
            self.citation(4, "7"), self.citation(4, False), None, "pricing.py:4", {},
        ]
        self.assertEqual(service.validate_citations(self.report, invalid + [good, good]), [good])

    def test_model_without_tool_evidence_is_rejected(self):
        with self.assertRaises(service.ModelError):
            self.model_answer([self.final_message()])

    def test_model_with_fabricated_citation_is_rejected(self):
        with self.assertRaises(service.ModelError):
            self.model_answer([self.tool_message(), self.final_message([self.citation(file="invented.py")])])

    def test_model_cannot_cite_lines_it_did_not_read(self):
        with self.assertRaises(service.ModelError):
            self.model_answer([
                self.tool_message(args={"file": "pricing.py", "line": 1, "end_line": 1}),
                self.final_message([self.citation(6)]),
            ])

    def test_failed_tool_does_not_count_as_reading_evidence(self):
        with self.assertRaises(service.ModelError):
            self.model_answer([
                self.tool_message(args={"file": "missing.py", "line": 1, "end_line": 8}),
                self.final_message(),
            ])

    def test_valid_read_then_structured_answer_is_accepted(self):
        result, request = self.model_answer([self.tool_message(), self.final_message()])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["citations"], [self.citation(6)])
        self.assertIn("test-model", result["mode"])
        self.assertEqual(result["tool_events"][0]["tool"], "read_source")

    def test_model_without_key_fails_before_network(self):
        missing = {**self.config, "configured": False, "enabled": False, "api_key": ""}
        with patch.object(service, "load_model_config", return_value=missing):
            with patch.object(service, "_request_completion") as request:
                with self.assertRaises(service.ModelError):
                    service.answer_question(self.report, "审查代码", use_model=True)
                request.assert_not_called()

    def test_invalid_model_json_is_reported_as_model_error(self):
        with self.assertRaises(service.ModelError):
            self.model_answer([self.tool_message(), {"content": "Not structured JSON"}])

    def test_bounded_model_loop_stops_without_final_answer(self):
        with self.assertRaises(service.ModelError):
            self.model_answer([self.tool_message()] * 5)


if __name__ == "__main__":
    unittest.main()
