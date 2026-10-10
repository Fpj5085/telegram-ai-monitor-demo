"""Fail closed when the public demo tree contains unapproved or risky files."""

from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "PUBLIC_ALLOWLIST.txt"

SECRET_PATTERNS = {
    "credential-like token": re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"\d{6,}:[A-Za-z0-9_-]{25,})\b"
    ),
    "private key material": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
    "credential assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|context[_-]?token|bot[_-]?token|cookie)"
        r"\s*[:=]\s*[\"'][^\"'\r\n]{12,}[\"']"
    ),
    "personal or internal Windows path": re.compile(
        r"(?i)\b[A-Z]:\\(?:Users|OpenClaw|Temp)\\[^\s\"'<>|]+"
    ),
    "private network address": re.compile(
        r"\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.(?:\d{1,3}\.)\d{1,3}|"
        r"172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3})\b"
    ),
}

DISALLOWED_IMPORTS = {
    "telethon", "telegram", "requests", "httpx", "openai", "anthropic",
    "itchat", "wechatpy", "playwright", "socket", "smtplib", "ftplib",
}
DISALLOWED_MODULES = {"urllib.request", "urllib3", "aiohttp", "websockets"}


def _read_allowlist() -> set[str]:
    entries = {
        line.strip().replace("\\", "/")
        for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if len(entries) != sum(
        1 for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ):
        raise ValueError("allowlist contains duplicate paths")
    return entries


def _actual_files() -> tuple[set[str], list[str]]:
    files: set[str] = set()
    issues: list[str] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] == ".git" or "__pycache__" in relative.parts:
            continue
        if path.is_symlink():
            issues.append(f"symlink is not publishable: {relative.as_posix()}")
        elif path.is_file():
            files.add(relative.as_posix())
    return files, issues


def _check_fixture_markers(issues: list[str]) -> None:
    fixture_path = ROOT / "telegram_ai_monitor_demo/fixtures/channel_messages.json"
    messages = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(messages, list) or not messages:
        issues.append("synthetic channel fixture is missing or empty")
        return
    for index, message in enumerate(messages):
        if (
            not isinstance(message, dict)
            or not str(message.get("message_id", "")).startswith("sim-msg-")
            or not str(message.get("channel", "")).startswith("demo_channel_")
            or "[SIMULATED]" not in str(message.get("text", ""))
        ):
            issues.append(f"synthetic marker missing from fixture row {index + 1}")
    digest = (ROOT / "telegram_ai_monitor_demo/fixtures/saved_digest.md").read_text(
        encoding="utf-8"
    )
    if "纯演示内容" not in digest:
        issues.append("saved digest lacks an explicit synthetic-data marker")


def _check_python_imports(path: Path, relative: str, issues: list[str]) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    except (SyntaxError, UnicodeError) as exc:
        issues.append(f"cannot parse Python file {relative}: {type(exc).__name__}")
        return
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            root = module.split(".", 1)[0]
            if root in DISALLOWED_IMPORTS or module in DISALLOWED_MODULES:
                issues.append(f"external-service integration import in {relative}: {module}")


def main() -> int:
    issues: list[str] = []
    try:
        approved = _read_allowlist()
    except (OSError, ValueError) as exc:
        print(f"FAIL: allowlist unreadable or invalid ({type(exc).__name__})")
        return 1

    actual, tree_issues = _actual_files()
    issues.extend(tree_issues)
    for relative in sorted(actual - approved):
        issues.append(f"unlisted file: {relative}")
    for relative in sorted(approved - actual):
        issues.append(f"allowlisted file missing: {relative}")

    for relative in sorted(actual & approved):
        path = ROOT / relative
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            issues.append(f"public file is not readable UTF-8: {relative} ({type(exc).__name__})")
            continue
        for category, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                issues.append(f"{category} pattern found in {relative}")
        if path.suffix == ".py":
            _check_python_imports(path, relative, issues)

    try:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        if project.get("project", {}).get("dependencies", []) != []:
            issues.append("Demo must not declare third-party runtime dependencies")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        issues.append(f"pyproject.toml invalid ({type(exc).__name__})")

    _check_fixture_markers(issues)
    if issues:
        for issue in issues:
            print(f"FAIL: {issue}")
        return 1
    print(f"PASS: {len(approved)} allowlisted files; no extra files, risky patterns, or live-service imports")
    return 0


if __name__ == "__main__":
    sys.exit(main())
