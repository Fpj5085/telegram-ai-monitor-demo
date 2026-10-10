"""SQLite persistence for the synthetic demonstration."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS digests (
                    digest_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('send_failed', 'sent')),
                    delivered_at REAL
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    alert_id TEXT PRIMARY KEY,
                    source_message_id TEXT NOT NULL UNIQUE,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'sent')),
                    delivered_at REAL
                );
                CREATE TABLE IF NOT EXISTS delivery_circuit (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    blocked_version TEXT NOT NULL,
                    business_code INTEGER NOT NULL,
                    opened_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS delivery_throttle (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    last_alert_attempt REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS delivery_lock (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    owner TEXT NOT NULL,
                    lease_until REAL NOT NULL
                );
                """
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def save_digest_once(self, digest_id: str, content: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO digests(digest_id, content, status) VALUES (?, ?, 'send_failed')",
                (digest_id, content),
            )
            return cur.rowcount == 1

    def save_alert_once(self, alert_id: str, source_message_id: str, content: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO alerts(alert_id, source_message_id, content, status) "
                "VALUES (?, ?, ?, 'queued')",
                (alert_id, source_message_id, content),
            )
            return cur.rowcount == 1

    def pending_digest(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT digest_id, content FROM digests WHERE status = 'send_failed' "
                "ORDER BY digest_id DESC LIMIT 1"
            ).fetchone()

    def mark_digest_sent(self, digest_id: str, now: float) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE digests SET status = 'sent', delivered_at = ? WHERE digest_id = ?",
                (now, digest_id),
            )

    def circuit_version(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT blocked_version FROM delivery_circuit WHERE singleton = 1"
            ).fetchone()
            return None if row is None else str(row["blocked_version"])

    def open_circuit(self, version: str, business_code: int, now: float) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO delivery_circuit(singleton, blocked_version, business_code, opened_at) "
                "VALUES (1, ?, ?, ?) "
                "ON CONFLICT(singleton) DO UPDATE SET "
                "blocked_version=excluded.blocked_version, "
                "business_code=excluded.business_code, opened_at=excluded.opened_at",
                (version, business_code, now),
            )

    def clear_circuit(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM delivery_circuit WHERE singleton = 1")

    def acquire_delivery_lock(self, owner: str, now: float, lease_seconds: float = 15) -> bool:
        """Atomically guard digest and alert delivery across independent processes."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner, lease_until FROM delivery_lock WHERE singleton = 1"
            ).fetchone()
            if row is not None and float(row["lease_until"]) > now:
                conn.rollback()
                return False
            conn.execute(
                "INSERT INTO delivery_lock(singleton, owner, lease_until) VALUES (1, ?, ?) "
                "ON CONFLICT(singleton) DO UPDATE SET owner=excluded.owner, "
                "lease_until=excluded.lease_until",
                (owner, now + lease_seconds),
            )
            conn.commit()
            return True

    def release_delivery_lock(self, owner: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM delivery_lock WHERE singleton = 1 AND owner = ?",
                (owner,),
            )

    def reserve_next_alert(self, now: float) -> sqlite3.Row | None:
        """Atomically reserve one alert attempt per 60-second window."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            throttle = conn.execute(
                "SELECT last_alert_attempt FROM delivery_throttle WHERE singleton = 1"
            ).fetchone()
            if throttle is not None and now - float(throttle["last_alert_attempt"]) < 60:
                conn.rollback()
                return None

            alert = conn.execute(
                "SELECT alert_id, content FROM alerts WHERE status = 'queued' "
                "ORDER BY alert_id LIMIT 1"
            ).fetchone()
            if alert is None:
                conn.commit()
                return None

            conn.execute(
                "INSERT INTO delivery_throttle(singleton, last_alert_attempt) VALUES (1, ?) "
                "ON CONFLICT(singleton) DO UPDATE SET last_alert_attempt=excluded.last_alert_attempt",
                (now,),
            )
            conn.commit()
            return alert

    def mark_alert_sent(self, alert_id: str, now: float) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE alerts SET status = 'sent', delivered_at = ? WHERE alert_id = ?",
                (now, alert_id),
            )

    def counts(self) -> dict[str, Any]:
        with self.connect() as conn:
            digest = conn.execute(
                "SELECT status, COUNT(*) AS n FROM digests GROUP BY status"
            ).fetchall()
            alerts = conn.execute(
                "SELECT status, COUNT(*) AS n FROM alerts GROUP BY status"
            ).fetchall()
            circuit = conn.execute(
                "SELECT blocked_version, business_code FROM delivery_circuit WHERE singleton = 1"
            ).fetchone()
        return {
            "digests": {str(row["status"]): int(row["n"]) for row in digest},
            "alerts": {str(row["status"]): int(row["n"]) for row in alerts},
            "circuit_open": circuit is not None,
            "circuit_code": None if circuit is None else int(circuit["business_code"]),
            "circuit_version": None if circuit is None else str(circuit["blocked_version"]),
        }
