"""Command-line interface for the offline monitor demo."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from .mock_wechat import MockWeChatServer
from .pipeline import collect, drain
from .store import Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _child(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "telegram_ai_monitor_demo", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _demo() -> int:
    temp_root: Path | None = None
    with tempfile.TemporaryDirectory(prefix="tg-monitor-demo-") as temp:
        root = Path(temp)
        temp_root = root
        db = root / "demo.sqlite3"
        context = root / "synthetic-context.meta"
        context.write_text("SIMULATED_CONTEXT_VERSION_A", encoding="ascii")

        print("[1/7] 合成采集与规则筛选（独立进程）")
        print(_child("collect", "--db", str(db)))
        print("SQLite after collect:")
        print(_child("status", "--db", str(db)))

        with MockWeChatServer() as server:
            print("[2/7] 首次发送：本地假接口返回业务错误 -2")
            print(_child("drain", "--db", str(db), "--context-file", str(context), "--endpoint", server.endpoint, "--now", "100000"))
            print("SQLite after business rejection:")
            print(_child("status", "--db", str(db)))

            print("[3/7] 同一上下文元数据版本：新进程启动后仍阻断")
            print(_child("drain", "--db", str(db), "--context-file", str(context), "--endpoint", server.endpoint, "--now", "100060"))
            print(f"本地接口累计请求数：{len(server.requests)}（应仍为 1）")

            before = context.stat()
            context.write_text("SIMULATED_CONTEXT_VERSION_B_CHANGED", encoding="ascii")
            os.utime(context, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))

            print("[4/7] 合成上下文文件元数据变化：先复用已保存日报，再发送一条告警")
            print(_child("drain", "--db", str(db), "--context-file", str(context), "--endpoint", server.endpoint, "--now", "100120"))
            print("SQLite after recovery:")
            print(_child("status", "--db", str(db)))

            print("[5/7] 30 秒后再次启动：告警发送被 SQLite 限速")
            print(_child("drain", "--db", str(db), "--context-file", str(context), "--endpoint", server.endpoint, "--now", "100150"))

            print("[6/7] 后续模拟分钟继续逐条补发")
            print(_child("drain", "--db", str(db), "--context-file", str(context), "--endpoint", server.endpoint, "--now", "100180"))
            print(_child("drain", "--db", str(db), "--context-file", str(context), "--endpoint", server.endpoint, "--now", "100240"))
            print(f"本地模拟接口总请求数：{len(server.requests)}；告警请求数：{sum(r['kind'] == 'alert' for r in server.requests)}")
            print("SQLite final state:")
            print(_child("status", "--db", str(db)))

    print(f"[7/7] temporary_state_cleaned={temp_root is not None and not temp_root.exists()}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="脱敏、离线的 Telegram AI Monitor 演示")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="完整演示采集、筛选、熔断和恢复")

    collect_parser = sub.add_parser("collect", help="从合成 fixture 采集并筛选")
    collect_parser.add_argument("--db", required=True, type=Path)

    drain_parser = sub.add_parser("drain", help="使用本地模拟接口处理日报和一条告警")
    drain_parser.add_argument("--db", required=True, type=Path)
    drain_parser.add_argument("--context-file", required=True, type=Path)
    drain_parser.add_argument("--endpoint", required=True)
    drain_parser.add_argument("--now", type=float)

    status_parser = sub.add_parser("status", help="查看演示 SQLite 的非正文状态")
    status_parser.add_argument("--db", required=True, type=Path)

    args = parser.parse_args(argv)
    if args.command == "demo":
        return _demo()
    if args.command == "collect":
        print(json.dumps(collect(args.db), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "drain":
        print(json.dumps(drain(args.db, args.context_file, args.endpoint, args.now), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "status":
        print(json.dumps(Store(args.db).counts(), ensure_ascii=False, sort_keys=True))
        return 0
    parser.error("未知子命令")
    return 2
