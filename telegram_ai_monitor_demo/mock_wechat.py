"""A deliberately local-only HTTP endpoint used by the demo."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit


class _Handler(BaseHTTPRequestHandler):
    server: "MockWeChatServer"

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/mock/weixin/send":
            self.send_error(404)
            return
        if self.headers.get("Authorization") or self.headers.get("Cookie"):
            self.send_error(400)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 16_384:
                self.send_error(400)
                return
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return

        if not isinstance(payload, dict) or set(payload) != {"kind", "message_id", "text"}:
            self.send_error(400)
            return

        with self.server.state_lock:
            self.server.requests.append(payload)
            if self.server.fail_next:
                self.server.fail_next = False
                response = {"business_code": -2, "message": "SIMULATED_SESSION_EXPIRED"}
            else:
                response = {"business_code": 0, "message": "SIMULATED_ACCEPTED"}
        if self.server.delay_seconds:
            time.sleep(self.server.delay_seconds)

        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class MockWeChatServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, delay_seconds: float = 0) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.fail_next = True
        self.delay_seconds = delay_seconds
        self.requests: list[dict[str, str]] = []
        self.state_lock = threading.Lock()
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}/mock/weixin/send"

    def __enter__(self) -> "MockWeChatServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


def validate_local_endpoint(endpoint: str) -> tuple[str, int, str]:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.path != "/mock/weixin/send"
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("演示接口只允许 http://127.0.0.1:<port>/mock/weixin/send")
    return parsed.hostname, parsed.port, parsed.path
