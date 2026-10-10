"""Synthetic collection, deterministic filtering and delivery recovery."""

from __future__ import annotations

import http.client
import json
import time
from uuid import uuid4
from pathlib import Path
from typing import Any

from .mock_wechat import validate_local_endpoint
from .store import Store

PACKAGE_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = PACKAGE_DIR / "fixtures"
DIGEST_ID = "demo-digest-2026-10-10"


def context_file_version(path: Path) -> str:
    """Return a non-secret metadata version; never open/read the file."""
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def collect(db_path: Path) -> dict[str, int | bool]:
    messages = json.loads((FIXTURES_DIR / "channel_messages.json").read_text(encoding="utf-8"))
    digest = (FIXTURES_DIR / "saved_digest.md").read_text(encoding="utf-8")
    store = Store(db_path)
    saved_digest = store.save_digest_once(DIGEST_ID, digest)

    read_count = len(messages)
    accepted = 0
    filtered = 0
    for message in messages:
        text = str(message["text"])
        normalized = text.casefold()
        blocked = any(term in normalized for term in ("sponsored", "promotion", "advertisement"))
        eligible = "python" in normalized and any(
            marker in normalized for marker in ("released", "available")
        )
        if blocked or not eligible:
            filtered += 1
            continue

        alert_id = f"demo-alert-{message['message_id']}"
        alert_text = f"【虚构告警】{message['channel']}：{text}"
        accepted += int(store.save_alert_once(alert_id, str(message["message_id"]), alert_text))

    return {
        "task_status": "completed",
        "messages_read": read_count,
        "alerts_enqueued": accepted,
        "messages_filtered": filtered,
        "digest_created": saved_digest,
    }


def _post_local(endpoint: str, kind: str, message_id: str, text: str) -> tuple[int, int | None]:
    host, port, path = validate_local_endpoint(endpoint)
    body = json.dumps(
        {"kind": kind, "message_id": message_id, "text": text},
        ensure_ascii=False,
    ).encode("utf-8")
    conn = http.client.HTTPConnection(host, port, timeout=3)
    try:
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        raw = response.read(16_384)
        if response.status != 200:
            return response.status, None
        parsed: Any = json.loads(raw)
        if not isinstance(parsed, dict):
            return response.status, None
        code = parsed.get("business_code")
        if isinstance(code, bool) or not isinstance(code, int):
            return response.status, None
        return response.status, code
    finally:
        conn.close()


def drain(db_path: Path, context_path: Path, endpoint: str, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    store = Store(db_path)
    lock_owner = uuid4().hex
    if not store.acquire_delivery_lock(lock_owner, now):
        return {
            "result": "delivery_lock_busy",
            "task_status": "completed",
            "delivery_status": "deferred",
            "requests": 0,
        }
    try:
        return _drain_locked(store, context_path, endpoint, now)
    finally:
        store.release_delivery_lock(lock_owner)


def _drain_locked(
    store: Store,
    context_path: Path,
    endpoint: str,
    now: float,
) -> dict[str, Any]:
    request_count = 0
    version = context_file_version(context_path)
    blocked_version = store.circuit_version()

    if blocked_version == version:
        return {
            "result": "circuit_open",
            "task_status": "completed",
            "delivery_status": "deferred",
            "business_code": -2,
            "requests": 0,
            "version": version,
        }
    if blocked_version is not None:
        store.clear_circuit()

    digest = store.pending_digest()
    if digest is not None:
        request_count += 1
        try:
            http_status, code = _post_local(
                endpoint, "digest", str(digest["digest_id"]), str(digest["content"])
            )
        except (OSError, http.client.HTTPException, ValueError):
            return {
                "result": "digest_send_error",
                "task_status": "completed_with_error",
                "delivery_status": "transport_error",
                "requests": request_count,
                "version": version,
            }
        if http_status != 200 or code is None:
            return {
                "result": "digest_send_error",
                "task_status": "completed_with_error",
                "delivery_status": "invalid_response",
                "business_code": code,
                "requests": request_count,
                "version": version,
            }
        if code == -2:
            store.open_circuit(version, code, now)
            return {
                "result": "circuit_opened",
                "task_status": "completed",
                "delivery_status": "business_rejected",
                "business_code": code,
                "requests": request_count,
                "version": version,
            }
        if code != 0:
            return {
                "result": "digest_send_error",
                "task_status": "completed_with_error",
                "delivery_status": "business_rejected",
                "business_code": code,
                "requests": request_count,
                "version": version,
            }
        store.mark_digest_sent(str(digest["digest_id"]), now)

    alert = store.reserve_next_alert(now)
    if alert is None:
        state = store.counts()
        result = "alert_throttled" if state["alerts"].get("queued", 0) else "idle"
        delivery = "rate_limited" if result == "alert_throttled" else "idle"
        return {
            "result": result,
            "task_status": "completed",
            "delivery_status": delivery,
            "requests": request_count,
            "version": version,
        }

    request_count += 1
    try:
        http_status, code = _post_local(
            endpoint, "alert", str(alert["alert_id"]), str(alert["content"])
        )
    except (OSError, http.client.HTTPException, ValueError):
        return {
            "result": "alert_send_error",
            "task_status": "completed_with_error",
            "delivery_status": "transport_error",
            "requests": request_count,
            "version": version,
        }
    if http_status != 200 or code is None:
        return {
            "result": "alert_send_error",
            "task_status": "completed_with_error",
            "delivery_status": "invalid_response",
            "business_code": code,
            "requests": request_count,
            "version": version,
        }
    if code == -2:
        store.open_circuit(version, code, now)
        return {
            "result": "circuit_opened",
            "task_status": "completed",
            "delivery_status": "business_rejected",
            "business_code": code,
            "requests": request_count,
            "version": version,
        }
    if code != 0:
        return {
            "result": "alert_send_error",
            "task_status": "completed_with_error",
            "delivery_status": "business_rejected",
            "business_code": code,
            "requests": request_count,
            "version": version,
        }
    store.mark_alert_sent(str(alert["alert_id"]), now)
    return {
        "result": "sent_one_alert",
        "task_status": "completed",
        "delivery_status": "accepted_by_mock",
        "requests": request_count,
        "version": version,
    }
