"""A local-only mock OpenAI-compatible endpoint used for offline LLM verification."""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .llm_filter import MOCK_LLM_PATH
from .rules import RELEASE_MARKERS, TOPIC_TERM, PROMO_TERMS

# Commercial / non-tooling senses the deterministic demo rule cannot express.
MOCK_COMMERCIAL_TERMS = (
    "course", "discount", "offer", "deal", "giveaway", "raffle", "merchandise",
    "socks", "certification", "bootcamp", "paywall", "webinar", "marketing",
    "coupon", "sale", "job posting", "developer wanted", "adoption", "zoo",
    "monty", "streaming", "affiliate", "trial",
)


def _mock_verdict(text: str) -> dict[str, Any]:
    """Deterministic heuristic the mock model uses instead of real inference."""
    normalized = text.casefold()
    is_promo = any(term in normalized for term in PROMO_TERMS)
    commercial = any(term in normalized for term in MOCK_COMMERCIAL_TERMS)
    topic = TOPIC_TERM in normalized
    release = any(marker in normalized for marker in RELEASE_MARKERS)
    relevant = topic and release and not is_promo and not commercial
    category = "other"
    if relevant:
        category = "release" if "released" in normalized else "tooling"
    elif is_promo:
        category = "other"
    else:
        category = "discussion"
    priority = "high" if relevant and "2.0" in normalized else ("medium" if relevant else "low")
    return {
        "relevant": relevant,
        "category": category,
        "priority": priority,
        "value_note": "模拟判定，仅用于离线验证"[:60],
    }


class _Handler(BaseHTTPRequestHandler):
    server: "MockLLMServer"

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != MOCK_LLM_PATH:
            self.send_error(404)
            return
        if self.server.mode == "latency":
            time.sleep(self.server.latency_seconds)
        if self.server.mode == "http_429":
            self.send_response(429)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.server.mode == "http_500":
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            text = payload["messages"][1]["content"]
            model = payload["model"]
        except (ValueError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            self.send_error(400)
            return
        with self.server.state_lock:
            self.server.requests.append({"model": model, "text": text})
            if self.server.mode == "bad_json":
                content = "这不是JSON"
            elif self.server.mode == "missing_field":
                content = json.dumps({"relevant": True, "category": "release"})
            elif self.server.mode == "wrong_enum":
                content = json.dumps(
                    {"relevant": True, "category": "hype", "priority": "urgent", "value_note": "x"}
                )
            else:
                content = json.dumps(_mock_verdict(text), ensure_ascii=False)
            prompt_tokens = max(1, len(text) // 4)
            completion_tokens = max(1, len(content) // 4)
        body = json.dumps(
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class MockLLMServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        mode: str = "answer",
        latency_seconds: float = 0,
    ) -> None:
        if mode not in {"answer", "bad_json", "missing_field", "wrong_enum", "http_429", "http_500", "latency"}:
            raise ValueError(f"unknown mock mode: {mode}")
        super().__init__(("127.0.0.1", 0), _Handler)
        self.mode = mode
        self.latency_seconds = latency_seconds
        self.requests: list[dict[str, str]] = []
        self.state_lock = threading.Lock()
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}{MOCK_LLM_PATH}"

    def __enter__(self) -> "MockLLMServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)
