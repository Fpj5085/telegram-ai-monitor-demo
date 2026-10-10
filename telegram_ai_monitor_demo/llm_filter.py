"""Optional OpenAI-compatible LLM screening with strict local defaults.

The LLM only ever produces a *suggestion*. Whether a message is enqueued is
always decided by deterministic code after validating the suggestion against an
explicit schema. When the model call fails (bad JSON, missing fields, timeout,
rate limit, HTTP error), the deterministic rule result is used as the fallback.
"""

from __future__ import annotations

import http.client
import json
import re
import time
from typing import Any
from urllib.parse import urlsplit

from .rules import rule_screen

MOCK_LLM_PATH = "/mock/llm/v1/chat/completions"

SYSTEM_PROMPT = (
    "你是消息筛选助手。对给定的虚构频道消息，判断它是否值得推送给订阅者。\n"
    "判定标准：与 Python 工具、库或工作流的发布/可用性相关，且不是广告或闲聊。\n"
    "只输出一个 JSON 对象，不要输出任何其他文字，字段为：\n"
    'relevant（布尔）、category（字符串：release/tooling/discussion/other）、'
    "priority（字符串：high/medium/low）、value_note（字符串，20字以内）。"
)

VERDICT_SCHEMA = {
    "type": "object",
    "required": ["relevant", "category", "priority", "value_note"],
    "properties": {
        "relevant": {"type": "boolean"},
        "category": {"type": "string", "enum": ["release", "tooling", "discussion", "other"]},
        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
        "value_note": {"type": "string", "maxLength": 60},
    },
    "additionalProperties": False,
}

_ALLOWED_CATEGORIES = {"release", "tooling", "discussion", "other"}
_ALLOWED_PRIORITIES = {"high", "medium", "low"}


class LLMFilterError(Exception):
    """Raised when the model response cannot be trusted; caller must fall back."""


def validate_llm_endpoint(endpoint: str, allow_remote: bool = False) -> tuple[str, int, str]:
    """Only the local mock route by default; remote needs an explicit opt-in."""
    parsed = urlsplit(endpoint)
    if parsed.scheme not in ("http", "https") or parsed.port is None:
        raise ValueError("LLM endpoint must be an explicit http(s) URL with a port")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("LLM endpoint must not embed credentials, query or fragments")
    if allow_remote:
        if parsed.scheme != "https" or parsed.path != "/v1/chat/completions":
            raise ValueError("remote LLM endpoint must be https and end with /v1/chat/completions")
    else:
        if parsed.hostname != "127.0.0.1" or parsed.scheme != "http":
            raise ValueError("默认只允许 http://127.0.0.1 的 mock LLM；远端需显式 allow_remote")
        if parsed.path != MOCK_LLM_PATH:
            raise ValueError(f"mock LLM endpoint path must be {MOCK_LLM_PATH}")
    return parsed.hostname, parsed.port, parsed.path


def validate_verdict(payload: Any) -> dict[str, Any]:
    """Validate a parsed model response against the demo verdict schema."""
    if not isinstance(payload, dict):
        raise LLMFilterError("verdict is not a JSON object")
    missing = [key for key in VERDICT_SCHEMA["required"] if key not in payload]
    if missing:
        raise LLMFilterError(f"verdict missing fields: {', '.join(missing)}")
    extra = set(payload) - set(VERDICT_SCHEMA["required"])
    if extra:
        raise LLMFilterError(f"verdict has unexpected fields: {', '.join(sorted(extra))}")
    if isinstance(payload["relevant"], bool) is False:
        raise LLMFilterError("relevant must be a boolean")
    if payload["category"] not in _ALLOWED_CATEGORIES:
        raise LLMFilterError("category is not an allowed value")
    if payload["priority"] not in _ALLOWED_PRIORITIES:
        raise LLMFilterError("priority is not an allowed value")
    note = payload["value_note"]
    if not isinstance(note, str) or len(note) > 60:
        raise LLMFilterError("value_note must be a string of at most 60 characters")
    return {
        "relevant": bool(payload["relevant"]),
        "category": str(payload["category"]),
        "priority": str(payload["priority"]),
        "value_note": note,
    }


def _extract_verdict(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("`"):
        text = re.sub(r"^``(?:json)?\s*|``$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMFilterError(f"model content is not valid JSON: {exc.msg}") from exc
    return validate_verdict(parsed)


def classify_message(
    endpoint: str,
    model: str,
    text: str,
    timeout: float = 5.0,
    allow_remote: bool = False,
    api_key_env: str | None = None,
) -> dict[str, Any]:
    """Call an OpenAI-compatible chat completions endpoint and return a validated verdict."""
    import os

    host, port, path = validate_llm_endpoint(endpoint, allow_remote)
    headers = {"Content-Type": "application/json"}
    if api_key_env:
        value = os.environ.get(api_key_env, "")
        if value:
            headers["Authorization"] = f"Bearer {value}"
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read(65_536)
        if response.status == 429:
            raise LLMFilterError("model endpoint rate limited (HTTP 429)")
        if response.status != 200:
            raise LLMFilterError(f"model endpoint returned HTTP {response.status}")
        try:
            parsed: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMFilterError("model response body is not valid JSON") from exc
        try:
            content = parsed["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMFilterError("model response has unexpected OpenAI-compatible shape") from exc
        verdict = _extract_verdict(content)
        usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
        verdict["token_usage"] = {
            "prompt": int(usage.get("prompt_tokens", 0) or 0),
            "completion": int(usage.get("completion_tokens", 0) or 0),
        }
        return verdict
    except (OSError, http.client.HTTPException) as exc:
        raise LLMFilterError(f"model endpoint transport failure: {type(exc).__name__}") from exc
    finally:
        conn.close()


def decide_execution(verdict: dict[str, Any], rule_accepted: bool) -> bool:
    """Deterministic execution policy: never let an unvalidated suggestion act."""
    if not rule_accepted:
        return False
    return bool(verdict.get("relevant")) and verdict.get("priority") in ("high", "medium")


def screen_message(
    endpoint: str,
    model: str,
    text: str,
    timeout: float = 5.0,
    allow_remote: bool = False,
    api_key_env: str | None = None,
) -> dict[str, Any]:
    """Screen one message: model suggestion first, deterministic rule as fallback."""
    rule_accepted = rule_screen(text)
    started = time.perf_counter()
    try:
        verdict = classify_message(
            endpoint, model, text, timeout=timeout, allow_remote=allow_remote,
            api_key_env=api_key_env,
        )
        latency = time.perf_counter() - started
        return {
            "source": "llm",
            "suggestion": verdict,
            "executed": decide_execution(verdict, rule_accepted),
            "fallback_used": False,
            "rule_accepted": rule_accepted,
            "latency_seconds": latency,
            "error": None,
        }
    except LLMFilterError as exc:
        latency = time.perf_counter() - started
        return {
            "source": "rule_fallback",
            "suggestion": None,
            "executed": rule_accepted,
            "fallback_used": True,
            "rule_accepted": rule_accepted,
            "latency_seconds": latency,
            "error": str(exc),
        }
