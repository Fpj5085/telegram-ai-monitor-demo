"""Compare rule-only, LLM-only and hybrid screening on the synthetic dataset.

The LLM is by default the local mock endpoint, so the whole evaluation runs
offline. Against a real model, pass --endpoint/--model/--api-key-env with
--allow-remote and record the exact run conditions in the report.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_ai_monitor_demo.llm_filter import LLMFilterError, classify_message, decide_execution
from telegram_ai_monitor_demo.mock_llm import MockLLMServer
from telegram_ai_monitor_demo.rules import rule_screen

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "telegram_ai_monitor_demo"
    / "fixtures"
    / "eval_messages.json"
)


def _metrics(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
    tp = sum(1 for truth, pred in pairs if truth and pred)
    fp = sum(1 for truth, pred in pairs if not truth and pred)
    fn = sum(1 for truth, pred in pairs if truth and not pred)
    tn = sum(1 for truth, pred in pairs if not truth and not pred)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def evaluate(dataset: list[dict[str, Any]], endpoint: str, model: str,
             timeout: float, allow_remote: bool, api_key_env: str | None) -> dict[str, Any]:
    rule_pairs: list[tuple[bool, bool]] = []
    llm_pairs: list[tuple[bool, bool]] = []
    hybrid_pairs: list[tuple[bool, bool]] = []
    latencies: list[float] = []
    fallbacks = 0
    json_valid = 0
    model_calls = 0
    prompt_tokens = 0
    completion_tokens = 0

    for row in dataset:
        truth = bool(row["true_relevant"])
        text = str(row["text"])
        rule_ok = rule_screen(text)
        rule_pairs.append((truth, rule_ok))

        model_calls += 1
        started = time.perf_counter()
        try:
            verdict = classify_message(
                endpoint, model, text, timeout=timeout,
                allow_remote=allow_remote, api_key_env=api_key_env,
            )
            latency = time.perf_counter() - started
            latencies.append(latency)
            json_valid += 1
            usage = verdict.pop("token_usage", {})
            prompt_tokens += int(usage.get("prompt", 0))
            completion_tokens += int(usage.get("completion", 0))
            llm_pairs.append((truth, bool(verdict["relevant"])))
            hybrid_pairs.append((truth, decide_execution(verdict, rule_ok)))
        except LLMFilterError:
            latency = time.perf_counter() - started
            latencies.append(latency)
            fallbacks += 1
            llm_pairs.append((truth, rule_ok))
            hybrid_pairs.append((truth, rule_ok))

    def _stats(values: list[float]) -> dict[str, float]:
        if not values:
            return {"mean": 0.0, "p95": 0.0}
        ordered = sorted(values)
        p95 = ordered[min(len(ordered) - 1, int(round(0.95 * len(ordered))) - 1)]
        return {"mean": round(sum(ordered) / len(ordered), 4), "p95": round(p95, 4)}

    return {
        "rule_only": _metrics(rule_pairs),
        "llm_only": _metrics(llm_pairs),
        "hybrid": _metrics(hybrid_pairs),
        "run_stats": {
            "model_calls": model_calls,
            "json_valid": json_valid,
            "fallback_used": fallbacks,
            "latency_seconds": _stats(latencies),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=None, help="defaults to a fresh local mock server")
    parser.add_argument("--model", default="mock-model")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    dataset = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if args.endpoint is None:
        with MockLLMServer() as server:
            results = evaluate(
                dataset, server.endpoint, "mock-model", args.timeout, False, None
            )
    else:
        results = evaluate(
            dataset, args.endpoint, args.model, args.timeout,
            args.allow_remote, args.api_key_env,
        )

    results["dataset"] = {
        "path": "telegram_ai_monitor_demo/fixtures/eval_messages.json",
        "rows": len(dataset),
        "synthetic": True,
        "ground_truth": "constructed by scripts/generate_eval_dataset.py; not human-annotated",
    }
    output_text = json.dumps(results, ensure_ascii=False, indent=2)
    print(output_text)
    if args.output:
        args.output.write_text(output_text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
