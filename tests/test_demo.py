from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from telegram_ai_monitor_demo.mock_wechat import MockWeChatServer, validate_local_endpoint
from telegram_ai_monitor_demo.pipeline import context_file_version

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _command(*args: str) -> list[str]:
    return [sys.executable, "-m", "telegram_ai_monitor_demo", *args]


def run_cli(*args: str) -> dict[str, object]:
    completed = subprocess.run(
        _command(*args),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    return json.loads(completed.stdout)


def run_cli_parallel(*argument_sets: tuple[str, ...]) -> list[dict[str, object]]:
    processes = [
        subprocess.Popen(
            _command(*args),
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for args in argument_sets
    ]
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        if process.returncode != 0:
            raise AssertionError(f"child failed ({process.returncode}): {stderr}")
        results.append(json.loads(stdout))
    return results


class DemoTests(unittest.TestCase):
    def test_endpoint_accepts_only_exact_loopback_route(self) -> None:
        host, port, path = validate_local_endpoint(
            "http://127.0.0.1:8765/mock/weixin/send"
        )
        self.assertEqual((host, port, path), ("127.0.0.1", 8765, "/mock/weixin/send"))
        for endpoint in (
            "http://example.com:8080/mock/weixin/send",
            "http://127.0.0.2:8080/mock/weixin/send",
            "https://127.0.0.1:8080/mock/weixin/send",
            "http://127.0.0.1:8080/real/send",
            "http://user:pass@127.0.0.1:8080/mock/weixin/send",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_local_endpoint(endpoint)

    def test_context_version_uses_stat_without_opening_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "synthetic.meta"
            path.write_text("SIMULATED_CONTEXT_VERSION_A", encoding="ascii")
            with patch.object(Path, "open", side_effect=AssertionError("must not read")):
                first = context_file_version(path)
            with path.open("a", encoding="ascii") as handle:
                handle.write("_CHANGED")
            second = context_file_version(path)
            self.assertNotEqual(first, second)

    def test_cross_process_circuit_digest_reuse_and_exact_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "demo.sqlite3"
            context = root / "synthetic-context.meta"
            context.write_text("SIMULATED_CONTEXT_VERSION_A", encoding="ascii")

            collected = run_cli("collect", "--db", str(db))
            self.assertEqual(collected["messages_read"], 5)
            self.assertEqual(collected["alerts_enqueued"], 3)
            self.assertEqual(collected["messages_filtered"], 2)
            self.assertTrue(collected["digest_created"])
            self.assertEqual(collected["task_status"], "completed")

            second_collect = run_cli("collect", "--db", str(db))
            self.assertFalse(second_collect["digest_created"])
            self.assertEqual(second_collect["alerts_enqueued"], 0)
            before_send = run_cli("status", "--db", str(db))
            self.assertEqual(before_send["digests"], {"send_failed": 1})
            self.assertEqual(before_send["alerts"], {"queued": 3})

            with MockWeChatServer() as server:
                first = run_cli(
                    "drain", "--db", str(db), "--context-file", str(context),
                    "--endpoint", server.endpoint, "--now", "100000",
                )
                self.assertEqual(first["result"], "circuit_opened")
                self.assertEqual(first["task_status"], "completed")
                self.assertEqual(first["delivery_status"], "business_rejected")
                self.assertEqual(first["business_code"], -2)
                self.assertEqual(server.requests[0]["kind"], "digest")

                after_failure = run_cli("status", "--db", str(db))
                self.assertTrue(after_failure["circuit_open"])
                self.assertEqual(after_failure["circuit_code"], -2)
                self.assertEqual(after_failure["digests"], {"send_failed": 1})
                self.assertEqual(after_failure["alerts"], {"queued": 3})

                restarted = run_cli(
                    "drain", "--db", str(db), "--context-file", str(context),
                    "--endpoint", server.endpoint, "--now", "100060",
                )
                self.assertEqual(restarted["result"], "circuit_open")
                self.assertEqual(restarted["requests"], 0)
                self.assertEqual(len(server.requests), 1)

                context.write_text("SIMULATED_CONTEXT_VERSION_B_CHANGED", encoding="ascii")
                recovered = run_cli(
                    "drain", "--db", str(db), "--context-file", str(context),
                    "--endpoint", server.endpoint, "--now", "100120",
                )
                self.assertEqual(recovered["result"], "sent_one_alert")
                self.assertEqual(recovered["delivery_status"], "accepted_by_mock")
                self.assertEqual(recovered["requests"], 2)
                self.assertEqual(
                    [request["kind"] for request in server.requests],
                    ["digest", "digest", "alert"],
                )
                self.assertEqual(server.requests[0]["text"], server.requests[1]["text"])
                after_recovery = run_cli("status", "--db", str(db))
                self.assertFalse(after_recovery["circuit_open"])
                self.assertEqual(after_recovery["digests"], {"sent": 1})
                self.assertEqual(after_recovery["alerts"], {"queued": 2, "sent": 1})

                before_minute = run_cli(
                    "drain", "--db", str(db), "--context-file", str(context),
                    "--endpoint", server.endpoint, "--now", "100179",
                )
                self.assertEqual(before_minute["result"], "alert_throttled")
                self.assertEqual(len(server.requests), 3)

                args = (
                    "drain", "--db", str(db), "--context-file", str(context),
                    "--endpoint", server.endpoint, "--now", "100180",
                )
                concurrent = run_cli_parallel(args, args)
                self.assertEqual(
                    sum(result["result"] == "sent_one_alert" for result in concurrent), 1
                )
                self.assertEqual(
                    sum(request["kind"] == "alert" for request in server.requests), 2
                )

                exactly_one_minute = run_cli(
                    "drain", "--db", str(db), "--context-file", str(context),
                    "--endpoint", server.endpoint, "--now", "100240",
                )
                self.assertEqual(exactly_one_minute["result"], "sent_one_alert")
                final_state = run_cli("status", "--db", str(db))
                self.assertEqual(final_state["alerts"], {"sent": 3})
                self.assertEqual(len(server.requests), 5)

    def test_parallel_recovery_cannot_duplicate_saved_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "demo.sqlite3"
            context = root / "synthetic-context.meta"
            context.write_text("SIMULATED_CONTEXT_VERSION_A", encoding="ascii")
            run_cli("collect", "--db", str(db))
            with MockWeChatServer(delay_seconds=0.2) as server:
                args = (
                    "drain", "--db", str(db), "--context-file", str(context),
                    "--endpoint", server.endpoint, "--now", "200000",
                )
                results = run_cli_parallel(args, args)
                self.assertEqual(len(server.requests), 1)
                self.assertEqual(server.requests[0]["kind"], "digest")
                self.assertEqual(
                    sum(result["result"] == "circuit_opened" for result in results), 1
                )
                self.assertTrue(
                    any(result["result"] in {"delivery_lock_busy", "circuit_open"} for result in results)
                )
                state = run_cli("status", "--db", str(db))
                self.assertTrue(state["circuit_open"])
                self.assertEqual(state["digests"], {"send_failed": 1})
                self.assertEqual(state["alerts"], {"queued": 3})

    def test_demo_runs_end_to_end_and_cleans_temporary_state(self) -> None:
        completed = subprocess.run(
            _command("demo"),
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertIn('"business_code": -2', completed.stdout)
        self.assertIn('"result": "circuit_open"', completed.stdout)
        self.assertIn('"result": "alert_throttled"', completed.stdout)
        self.assertIn('"alerts": {"sent": 3}', completed.stdout)
        self.assertIn("temporary_state_cleaned=True", completed.stdout)

    def test_public_tree_allowlist_and_safety_audit(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/check_public_tree.py"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        self.assertIn("PASS: 18 allowlisted files", completed.stdout)


if __name__ == "__main__":
    unittest.main()
