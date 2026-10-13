"""SQLite-backed retention task workflow with model lineage."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


TASK_STATUSES = {"OPEN", "IN_PROGRESS", "COMPLETED", "DISMISSED"}


class RetentionTaskStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS retention_tasks (
                task_id TEXT PRIMARY KEY,
                prediction_id TEXT NOT NULL UNIQUE,
                customer_key TEXT NOT NULL,
                model_sha256 TEXT NOT NULL,
                risk_band TEXT NOT NULL,
                owner TEXT NOT NULL,
                playbook TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_retention_tasks_status_updated
                ON retention_tasks(status, updated_at DESC);
            CREATE TABLE IF NOT EXISTS retention_task_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES retention_tasks(task_id)
            );
            CREATE INDEX IF NOT EXISTS idx_retention_task_events_task
                ON retention_task_events(task_id, event_id);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def create(
        self,
        *,
        prediction_id: str,
        customer_key: str,
        model_sha256: str,
        risk_band: str,
        owner: str,
        playbook: str,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        task_id = "ret-" + uuid4().hex[:16]
        try:
            self.connection.execute(
                """
                INSERT INTO retention_tasks (
                    task_id, prediction_id, customer_key, model_sha256, risk_band,
                    owner, playbook, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
                """,
                (
                    task_id,
                    prediction_id,
                    customer_key,
                    model_sha256,
                    risk_band,
                    owner,
                    playbook,
                    now,
                    now,
                ),
            )
            self.connection.execute(
                """INSERT INTO retention_task_events (
                       task_id, event_type, from_status, to_status, occurred_at
                   ) VALUES (?, 'CREATED', NULL, 'OPEN', ?)""",
                (task_id, now),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as error:
            raise ValueError("retention task already exists for this prediction") from error
        return self.get(task_id)

    def get(self, task_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM retention_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return dict(row)

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status is None:
            rows = self.connection.execute(
                "SELECT * FROM retention_tasks ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self.connection.execute(
                """SELECT * FROM retention_tasks
                   WHERE status = ? ORDER BY updated_at DESC LIMIT ?""",
                (status, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_status(self, task_id: str, status: str) -> dict[str, Any]:
        if status not in TASK_STATUSES:
            raise ValueError("unsupported retention task status")
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        current = self.get(task_id)
        cursor = self.connection.execute(
            "UPDATE retention_tasks SET status = ?, updated_at = ? WHERE task_id = ?",
            (status, now, task_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(task_id)
        self.connection.execute(
            """INSERT INTO retention_task_events (
                   task_id, event_type, from_status, to_status, occurred_at
               ) VALUES (?, 'STATUS_CHANGED', ?, ?, ?)""",
            (task_id, current["status"], status, now),
        )
        self.connection.commit()
        return self.get(task_id)

    def events(self, task_id: str) -> list[dict[str, Any]]:
        self.get(task_id)
        rows = self.connection.execute(
            """SELECT event_id, task_id, event_type, from_status, to_status, occurred_at
               FROM retention_task_events WHERE task_id = ? ORDER BY event_id""",
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def event_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM retention_task_events"
        ).fetchone()
        return int(row["count"])

    def status_counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM retention_tasks GROUP BY status"
        ).fetchall()
        observed = {str(row["status"]): int(row["count"]) for row in rows}
        return {status: observed.get(status, 0) for status in sorted(TASK_STATUSES)}

