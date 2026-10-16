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
        event_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(retention_task_events)")
        }
        if "from_owner" not in event_columns:
            self.connection.execute("ALTER TABLE retention_task_events ADD COLUMN from_owner TEXT")
        if "to_owner" not in event_columns:
            self.connection.execute("ALTER TABLE retention_task_events ADD COLUMN to_owner TEXT")
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
                       task_id, event_type, from_status, to_status, occurred_at,
                       from_owner, to_owner
                   ) VALUES (?, 'CREATED', NULL, 'OPEN', ?, NULL, ?)""",
                (task_id, now, owner),
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

    def list(
        self,
        *,
        status: str | None = None,
        risk_band: str | None = None,
        owner: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        parameters: list[Any] = []
        for column, value in (("status", status), ("risk_band", risk_band), ("owner", owner)):
            if value is not None:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"SELECT * FROM retention_tasks{where} ORDER BY updated_at DESC LIMIT ?",
            (*parameters, limit),
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
                   task_id, event_type, from_status, to_status, occurred_at,
                   from_owner, to_owner
               ) VALUES (?, 'STATUS_CHANGED', ?, ?, ?, ?, ?)""",
            (task_id, current["status"], status, now, current["owner"], current["owner"]),
        )
        self.connection.commit()
        return self.get(task_id)

    def update_owner(self, task_id: str, owner: str) -> dict[str, Any]:
        normalized = owner.strip()
        if not normalized or len(normalized) > 80:
            raise ValueError("owner must contain between 1 and 80 characters")
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        current = self.get(task_id)
        if current["owner"] == normalized:
            return current
        self.connection.execute(
            "UPDATE retention_tasks SET owner = ?, updated_at = ? WHERE task_id = ?",
            (normalized, now, task_id),
        )
        self.connection.execute(
            """INSERT INTO retention_task_events (
                   task_id, event_type, from_status, to_status, occurred_at,
                   from_owner, to_owner
               ) VALUES (?, 'OWNER_CHANGED', ?, ?, ?, ?, ?)""",
            (task_id, current["status"], current["status"], now, current["owner"], normalized),
        )
        self.connection.commit()
        return self.get(task_id)

    def update_statuses(self, task_ids: list[str], status: str) -> list[dict[str, Any]]:
        unique_ids = list(dict.fromkeys(task_ids))
        if status not in TASK_STATUSES:
            raise ValueError("unsupported retention task status")
        if not unique_ids or len(unique_ids) > 100:
            raise ValueError("task_ids must contain between 1 and 100 unique tasks")
        placeholders = ",".join("?" for _ in unique_ids)
        current_rows = self.connection.execute(
            f"SELECT * FROM retention_tasks WHERE task_id IN ({placeholders})", unique_ids
        ).fetchall()
        current_by_id = {row["task_id"]: dict(row) for row in current_rows}
        missing = [task_id for task_id in unique_ids if task_id not in current_by_id]
        if missing:
            raise KeyError(missing[0])
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with self.connection:
            for task_id in unique_ids:
                current = current_by_id[task_id]
                if current["status"] == status:
                    continue
                self.connection.execute(
                    "UPDATE retention_tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                    (status, now, task_id),
                )
                self.connection.execute(
                    """INSERT INTO retention_task_events (
                           task_id, event_type, from_status, to_status, occurred_at,
                           from_owner, to_owner
                       ) VALUES (?, 'STATUS_CHANGED', ?, ?, ?, ?, ?)""",
                    (task_id, current["status"], status, now, current["owner"], current["owner"]),
                )
        return [self.get(task_id) for task_id in unique_ids]

    def update_owners(self, task_ids: list[str], owner: str) -> list[dict[str, Any]]:
        normalized = owner.strip()
        unique_ids = list(dict.fromkeys(task_ids))
        if not normalized or len(normalized) > 80:
            raise ValueError("owner must contain between 1 and 80 characters")
        if not unique_ids or len(unique_ids) > 100:
            raise ValueError("task_ids must contain between 1 and 100 unique tasks")
        placeholders = ",".join("?" for _ in unique_ids)
        current_rows = self.connection.execute(
            f"SELECT * FROM retention_tasks WHERE task_id IN ({placeholders})", unique_ids
        ).fetchall()
        current_by_id = {row["task_id"]: dict(row) for row in current_rows}
        missing = [task_id for task_id in unique_ids if task_id not in current_by_id]
        if missing:
            raise KeyError(missing[0])
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with self.connection:
            for task_id in unique_ids:
                current = current_by_id[task_id]
                if current["owner"] == normalized:
                    continue
                self.connection.execute(
                    "UPDATE retention_tasks SET owner = ?, updated_at = ? WHERE task_id = ?",
                    (normalized, now, task_id),
                )
                self.connection.execute(
                    """INSERT INTO retention_task_events (
                           task_id, event_type, from_status, to_status, occurred_at,
                           from_owner, to_owner
                       ) VALUES (?, 'OWNER_CHANGED', ?, ?, ?, ?, ?)""",
                    (task_id, current["status"], current["status"], now, current["owner"], normalized),
                )
        return [self.get(task_id) for task_id in unique_ids]

    def update_many(
        self, task_ids: list[str], *, owner: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Apply owner and status changes together in one validated transaction."""
        unique_ids = list(dict.fromkeys(task_ids))
        normalized_owner = owner.strip() if owner is not None else None
        if not unique_ids or len(unique_ids) > 100:
            raise ValueError("task_ids must contain between 1 and 100 unique tasks")
        if normalized_owner is None and status is None:
            raise ValueError("owner or status is required")
        if normalized_owner is not None and (not normalized_owner or len(normalized_owner) > 80):
            raise ValueError("owner must contain between 1 and 80 characters")
        if status is not None and status not in TASK_STATUSES:
            raise ValueError("unsupported retention task status")
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self.connection.execute(
            f"SELECT * FROM retention_tasks WHERE task_id IN ({placeholders})", unique_ids
        ).fetchall()
        current_by_id = {row["task_id"]: dict(row) for row in rows}
        missing = [task_id for task_id in unique_ids if task_id not in current_by_id]
        if missing:
            raise KeyError(missing[0])
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with self.connection:
            for task_id in unique_ids:
                current = current_by_id[task_id]
                next_owner = normalized_owner if normalized_owner is not None else current["owner"]
                next_status = status if status is not None else current["status"]
                if next_owner == current["owner"] and next_status == current["status"]:
                    continue
                self.connection.execute(
                    "UPDATE retention_tasks SET owner = ?, status = ?, updated_at = ? WHERE task_id = ?",
                    (next_owner, next_status, now, task_id),
                )
                if next_owner != current["owner"]:
                    self.connection.execute(
                        """INSERT INTO retention_task_events (
                               task_id, event_type, from_status, to_status, occurred_at,
                               from_owner, to_owner
                           ) VALUES (?, 'OWNER_CHANGED', ?, ?, ?, ?, ?)""",
                        (task_id, current["status"], current["status"], now, current["owner"], next_owner),
                    )
                if next_status != current["status"]:
                    self.connection.execute(
                        """INSERT INTO retention_task_events (
                               task_id, event_type, from_status, to_status, occurred_at,
                               from_owner, to_owner
                           ) VALUES (?, 'STATUS_CHANGED', ?, ?, ?, ?, ?)""",
                        (task_id, current["status"], next_status, now, next_owner, next_owner),
                    )
        return [self.get(task_id) for task_id in unique_ids]

    def events(self, task_id: str) -> list[dict[str, Any]]:
        self.get(task_id)
        rows = self.connection.execute(
            """SELECT event_id, task_id, event_type, from_status, to_status, occurred_at,
                      from_owner, to_owner
               FROM retention_task_events WHERE task_id = ? ORDER BY event_id""",
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def event_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM retention_task_events"
        ).fetchone()
        return int(row["count"])

    def events_after(self, event_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return global task events after a cursor for lightweight live clients."""
        rows = self.connection.execute(
            """SELECT event_id, task_id, event_type, from_status, to_status, occurred_at,
                      from_owner, to_owner
               FROM retention_task_events WHERE event_id > ? ORDER BY event_id LIMIT ?""",
            (event_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_event_id(self) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(event_id), 0) AS event_id FROM retention_task_events"
        ).fetchone()
        return int(row["event_id"])

    def status_counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM retention_tasks GROUP BY status"
        ).fetchall()
        observed = {str(row["status"]): int(row["count"]) for row in rows}
        return {status: observed.get(status, 0) for status in sorted(TASK_STATUSES)}

    def owner_workloads(self) -> list[dict[str, Any]]:
        """Summarize active task volume and risk mix for each owner."""
        rows = self.connection.execute(
            """
            SELECT owner,
                   COUNT(*) AS active_count,
                   SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END) AS open_count,
                   SUM(CASE WHEN status = 'IN_PROGRESS' THEN 1 ELSE 0 END) AS in_progress_count,
                   SUM(CASE WHEN risk_band = 'CRITICAL' THEN 1 ELSE 0 END) AS critical_count,
                   SUM(CASE WHEN risk_band = 'HIGH' THEN 1 ELSE 0 END) AS high_count
            FROM retention_tasks
            WHERE status IN ('OPEN', 'IN_PROGRESS')
            GROUP BY owner
            ORDER BY critical_count DESC, high_count DESC, active_count DESC, owner
            """
        ).fetchall()
        return [dict(row) for row in rows]

