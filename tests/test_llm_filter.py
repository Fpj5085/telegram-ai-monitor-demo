from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from telegram_ai_monitor_demo.llm_filter import (
    LLMFilterError,
    SYSTEM_PROMPT,
    decide_execution,
    screen_message,
    validate_llm_endpoint,
    validate_verdict,
)
from telegram_ai_monitor_demo.mock_llm import MockLLMServer

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EndpointPolicyTests(unittest.TestCase):
    def test_default_policy_only_allows_local_mock_route(self) -> None:
        host, port, path = validate_llm_endpoint("http://127.0.0.1:9/mock/llm/v1/chat/completions")
        self.assertEqual(path, "/mock/llm/v1/chat/completions")

    def test_remote_endpoints_rejected_without_explicit_opt_in(self) -> None:
        for endpoint in (
            "http://api.example.com:443/v1/chat/completions",
            "https://api.example.com/v1/chat/completions",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_llm_endpoint(endpoint)

    def test_remote_requires_https_and_exact_path(self) -> None:
        self.assertEqual(
            validate_llm_endpoint("https://api.example.com:443/v1/chat/completions", allow_remote=True),
            ("api.example.com", 443, "/v1/chat/completions"),
        )
        for endpoint in (
            "http://api.example.com:443/v1/chat/completions",
            "https://api.example.com:443/v1/other",
            "https://user:pass@api.example.com:443/v1/chat/completions",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_llm_endpoint(endpoint, allow_remote=True)


class VerdictValidationTests(unittest.TestCase):
    def test_valid_verdict_passes(self) -> None:
        verdict = validate_verdict(
            {"relevant": True, "category": "release", "priority": "high", "value_note": "ok"}
        )
        self.assertTrue(verdict["relevant"])

    def test_missing_field_raises(self) -> None:
        with self.assertRaises(LLMFilterError):
            validate_verdict({"relevant": True, "category": "release"})

    def test_extra_field_raises(self) -> None:
        with self.assertRaises(LLMFilterError):
            validate_verdict(
                {"relevant": True, "category": "release", "priority": "high",
                 "value_note": "x", "action": "send_now"}
            )

    def test_wrong_types_and_enums_raise(self) -> None:
        for payload in (
            {"relevant": "yes", "category": "release", "priority": "high", "value_note": "x"},
            {"relevant": True, "category": "hype", "priority": "high", "value_note": "x"},
            {"relevant": True, "category": "release", "priority": "urgent", "value_note": "x"},
            {"relevant": True, "category": "release", "priority": "high", "value_note": "x" * 61},
        ):
            with self.subTest(payload=payload), self.assertRaises(LLMFilterError):
                validate_verdict(payload)


class MockServerModeTests(unittest.TestCase):
    def test_answer_mode_returns_validated_verdict(self) -> None:
        with MockLLMServer() as server:
            result = screen_message(server.endpoint, "mock-model", "[SIMULATED] Python tool released.")
        self.assertFalse(result["fallback_used"])
        self.assertTrue(result["suggestion"]["relevant"])
        self.assertIn("token_usage", result["suggestion"])

    def test_bad_json_mode_falls_back_to_rule(self) -> None:
        with MockLLMServer(mode="bad_json") as server:
            result = screen_message(
                server.endpoint, "mock-model", "[SIMULATED] Python tool released."
            )
        self.assertTrue(result["fallback_used"])
        self.assertTrue(result["executed"])
        self.assertIn("not valid JSON", result["error"])

    def test_missing_field_mode_falls_back(self) -> None:
        with MockLLMServer(mode="missing_field") as server:
            result = screen_message(
                server.endpoint, "mock-model", "[SIMULATED] Python tool released."
            )
        self.assertTrue(result["fallback_used"])
        self.assertIn("missing fields", result["error"])

    def test_wrong_enum_mode_falls_back(self) -> None:
        with MockLLMServer(mode="wrong_enum") as server:
            result = screen_message(
                server.endpoint, "mock-model", "[SIMULATED] Python tool released."
            )
        self.assertTrue(result["fallback_used"])
        self.assertIn("allowed value", result["error"])

    def test_http_429_and_500_fall_back(self) -> None:
        for mode in ("http_429", "http_500"):
            with self.subTest(mode=mode):
                with MockLLMServer(mode=mode) as server:
                    result = screen_message(
                        server.endpoint, "mock-model", "[SIMULATED] Python tool released."
                    )
                self.assertTrue(result["fallback_used"])
                self.assertTrue(result["executed"])

    def test_transport_timeout_falls_back(self) -> None:
        with MockLLMServer(mode="latency", latency_seconds=1.5) as server:
            result = screen_message(
                server.endpoint, "mock-model", "[SIMULATED] Python tool released.", timeout=0.2
            )
        self.assertTrue(result["fallback_used"])
        self.assertIn("transport failure", result["error"])

    def test_execution_policy_never_sends_without_rule_gate(self) -> None:
        verdict = {"relevant": True, "category": "release", "priority": "high", "value_note": "x"}
        self.assertFalse(decide_execution(verdict, rule_accepted=False))
        self.assertTrue(decide_execution(verdict, rule_accepted=True))
        low = dict(verdict, priority="low")
        self.assertFalse(decide_execution(low, rule_accepted=True))


class EvalScriptTests(unittest.TestCase):
    def test_eval_script_runs_offline_and_writes_report(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "report.json"
            subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "run_eval.py"), "--output", str(output)],
                cwd=PROJECT_ROOT, check=True, capture_output=True, text=True,
                encoding="utf-8", timeout=60,
            )
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["dataset"]["rows"], 82)
        self.assertTrue(report["dataset"]["synthetic"])
        self.assertEqual(report["run_stats"]["model_calls"], 82)
        for strategy in ("rule_only", "llm_only", "hybrid"):
            self.assertIn(strategy, report)
if __name__ == "__main__":
    unittest.main()
