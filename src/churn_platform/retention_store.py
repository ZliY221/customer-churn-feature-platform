"""SQLite-backed retention task workflow with model lineage."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


TASK_STATUSES = {"OPEN", "IN_PROGRESS", "COMPLETED", "DISMISSED"}
CONTACT_RESULTS = {"REACHED", "NO_RESPONSE", "INVALID_CONTACT", "DECLINED"}
CONTACT_CHANNELS = {"PHONE", "SMS", "EMAIL", "IN_APP", "OTHER"}
CUSTOMER_RESPONSES = {"POSITIVE", "NEUTRAL", "NEGATIVE", "UNKNOWN"}


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
            CREATE TABLE IF NOT EXISTS retention_outcomes (
                outcome_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                contact_result TEXT NOT NULL,
                contact_channel TEXT NOT NULL,
                customer_response TEXT NOT NULL,
                contacted_at TEXT NOT NULL,
                observed_at TEXT,
                retained_7d INTEGER,
                retained_30d INTEGER,
                note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES retention_tasks(task_id)
            );
            CREATE INDEX IF NOT EXISTS idx_retention_outcomes_updated
                ON retention_outcomes(updated_at DESC);
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

    def strategy_summary(self) -> list[dict[str, Any]]:
        """Summarize task volume and completion by the configured playbook."""
        rows = self.connection.execute(
            """
            SELECT playbook AS strategy, COUNT(*) AS task_count,
                   SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed_count,
                   SUM(CASE WHEN risk_band IN ('CRITICAL', 'HIGH') THEN 1 ELSE 0 END) AS high_priority_count
            FROM retention_tasks GROUP BY playbook
            ORDER BY high_priority_count DESC, task_count DESC, playbook
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def task_sla_summary(self, *, observed_at: datetime | None = None,
                         target_hours: int = 24) -> dict[str, Any]:
        """Classify active retention tasks against a bounded response target."""
        if target_hours <= 0:
            raise ValueError("target_hours must be positive")
        now = observed_at or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        rows = self.connection.execute(
            "SELECT task_id, created_at FROM retention_tasks WHERE status IN ('OPEN', 'IN_PROGRESS')"
        ).fetchall()
        overdue = 0
        due_soon = 0
        for row in rows:
            created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
            remaining = target_hours - (now - created).total_seconds() / 3600
            overdue += int(remaining <= 0)
            due_soon += int(0 < remaining <= 4)
        return {
            "active_count": len(rows), "overdue_count": overdue,
            "due_soon_count": due_soon,
            "on_track_count": len(rows) - overdue - due_soon,
            "target_hours": target_hours,
        }

    def upsert_outcome(
        self,
        task_id: str,
        *,
        contact_result: str,
        contact_channel: str,
        customer_response: str,
        contacted_at: str,
        observed_at: str | None = None,
        retained_7d: bool | None = None,
        retained_30d: bool | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Create or replace the observed result for one retention task."""
        task = self.get(task_id)
        if contact_result not in CONTACT_RESULTS:
            raise ValueError("unsupported contact result")
        if contact_channel not in CONTACT_CHANNELS:
            raise ValueError("unsupported contact channel")
        if customer_response not in CUSTOMER_RESPONSES:
            raise ValueError("unsupported customer response")
        normalized_note = note.strip() if note is not None else None
        if normalized_note == "":
            normalized_note = None
        if normalized_note is not None and len(normalized_note) > 500:
            raise ValueError("note must not exceed 500 characters")
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        existing = self.connection.execute(
            "SELECT outcome_id, created_at FROM retention_outcomes WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        outcome_id = existing["outcome_id"] if existing else "out-" + uuid4().hex[:16]
        created_at = existing["created_at"] if existing else now
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO retention_outcomes (
                    outcome_id, task_id, contact_result, contact_channel,
                    customer_response, contacted_at, observed_at, retained_7d,
                    retained_30d, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    contact_result = excluded.contact_result,
                    contact_channel = excluded.contact_channel,
                    customer_response = excluded.customer_response,
                    contacted_at = excluded.contacted_at,
                    observed_at = excluded.observed_at,
                    retained_7d = excluded.retained_7d,
                    retained_30d = excluded.retained_30d,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    outcome_id,
                    task_id,
                    contact_result,
                    contact_channel,
                    customer_response,
                    contacted_at,
                    observed_at,
                    None if retained_7d is None else int(retained_7d),
                    None if retained_30d is None else int(retained_30d),
                    normalized_note,
                    created_at,
                    now,
                ),
            )
            self.connection.execute(
                """INSERT INTO retention_task_events (
                       task_id, event_type, from_status, to_status, occurred_at,
                       from_owner, to_owner
                   ) VALUES (?, 'OUTCOME_RECORDED', ?, ?, ?, ?, ?)""",
                (task_id, task["status"], task["status"], now, task["owner"], task["owner"]),
            )
        return self.get_outcome(task_id)

    def get_outcome(self, task_id: str) -> dict[str, Any]:
        self.get(task_id)
        row = self.connection.execute(
            "SELECT * FROM retention_outcomes WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._serialize_outcome(row)

    def list_outcomes(self, *, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM retention_outcomes ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._serialize_outcome(row) for row in rows]

    def outcome_summary(self) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS recorded_count,
                   SUM(CASE WHEN contact_result = 'REACHED' THEN 1 ELSE 0 END) AS reached_count,
                   SUM(CASE WHEN retained_7d IS NOT NULL THEN 1 ELSE 0 END) AS observed_7d_count,
                   SUM(CASE WHEN retained_7d = 1 THEN 1 ELSE 0 END) AS retained_7d_count,
                   SUM(CASE WHEN retained_30d IS NOT NULL THEN 1 ELSE 0 END) AS observed_30d_count,
                   SUM(CASE WHEN retained_30d = 1 THEN 1 ELSE 0 END) AS retained_30d_count
            FROM retention_outcomes
            """
        ).fetchone()
        summary = {key: int(row[key] or 0) for key in row.keys()}
        summary["retention_7d_rate"] = (
            summary["retained_7d_count"] / summary["observed_7d_count"]
            if summary["observed_7d_count"]
            else None
        )
        summary["retention_30d_rate"] = (
            summary["retained_30d_count"] / summary["observed_30d_count"]
            if summary["observed_30d_count"]
            else None
        )
        return summary

    def strategy_effectiveness(self) -> list[dict[str, Any]]:
        """Compare mature retention observations across execution strategies."""
        rows = self.connection.execute(
            """
            SELECT t.playbook AS strategy, COUNT(o.outcome_id) AS recorded_count,
                   SUM(CASE WHEN o.retained_7d IS NOT NULL THEN 1 ELSE 0 END) AS observed_7d_count,
                   SUM(CASE WHEN o.retained_7d = 1 THEN 1 ELSE 0 END) AS retained_7d_count,
                   SUM(CASE WHEN o.retained_30d IS NOT NULL THEN 1 ELSE 0 END) AS observed_30d_count,
                   SUM(CASE WHEN o.retained_30d = 1 THEN 1 ELSE 0 END) AS retained_30d_count
            FROM retention_tasks t LEFT JOIN retention_outcomes o ON o.task_id = t.task_id
            GROUP BY t.playbook ORDER BY recorded_count DESC, t.playbook
            """
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = {key: int(row[key] or 0) if key != "strategy" else row[key] for key in row.keys()}
            item["retention_7d_rate"] = item["retained_7d_count"] / item["observed_7d_count"] if item["observed_7d_count"] else None
            item["retention_30d_rate"] = item["retained_30d_count"] / item["observed_30d_count"] if item["observed_30d_count"] else None
            items.append(item)
        return items

    def model_effectiveness(self) -> list[dict[str, Any]]:
        """Link model lineage and risk bands to observed retention results."""
        rows = self.connection.execute(
            """
            SELECT t.model_sha256, t.risk_band, COUNT(*) AS task_count,
                   SUM(CASE WHEN o.retained_30d IS NOT NULL THEN 1 ELSE 0 END) AS observed_30d_count,
                   SUM(CASE WHEN o.retained_30d = 1 THEN 1 ELSE 0 END) AS retained_30d_count
            FROM retention_tasks t LEFT JOIN retention_outcomes o ON o.task_id = t.task_id
            GROUP BY t.model_sha256, t.risk_band
            ORDER BY t.model_sha256, t.risk_band
            """
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["retention_30d_rate"] = item["retained_30d_count"] / item["observed_30d_count"] if item["observed_30d_count"] else None
            items.append(item)
        return items

    def priority_queue(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return an explainable queue ordered by risk and task age."""
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self.connection.execute(
            """SELECT *, CASE risk_band WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3
                      WHEN 'WATCH' THEN 2 ELSE 1 END AS risk_score
               FROM retention_tasks WHERE status IN ('OPEN', 'IN_PROGRESS')
               ORDER BY risk_score DESC, created_at, task_id LIMIT ?""", (limit,)
        ).fetchall()
        return [{**dict(row), "priority_reason": f"{row['risk_band']} risk / {row['status']} task"}
                for row in rows]

    def next_action(self, task_id: str) -> dict[str, Any]:
        """Recommend a bounded, human-reviewable action for one task."""
        task = self.get(task_id)
        channels = {"CRITICAL": "PHONE", "HIGH": "PHONE", "WATCH": "IN_APP"}
        windows = {"CRITICAL": 1, "HIGH": 4, "WATCH": 24}
        channel = channels.get(task["risk_band"], "EMAIL")
        return {"task_id": task_id, "recommended_channel": channel,
                "contact_within_hours": windows.get(task["risk_band"], 48),
                "playbook": task["playbook"], "requires_review": True,
                "reason": f"{task['risk_band']} risk uses {channel.lower()} follow-up"}

    def outcome_confidence(self) -> dict[str, Any]:
        """Grade how much mature outcome evidence is available for decisions."""
        summary = self.outcome_summary()
        mature = summary["observed_30d_count"]
        level = "HIGH" if mature >= 100 else "MEDIUM" if mature >= 30 else "LOW"
        return {"confidence": level, "observed_7d_count": summary["observed_7d_count"],
                "observed_30d_count": mature, "minimum_for_medium": 30,
                "minimum_for_high": 100,
                "decision_note": "Use mature 30-day outcomes before changing strategy"}

    def owner_capacity(self, *, target_per_owner: int = 20) -> list[dict[str, Any]]:
        """Compare active workload with an explicit operator capacity target."""
        if target_per_owner < 1:
            raise ValueError("target_per_owner must be positive")
        items = []
        for row in self.owner_workloads():
            active = int(row["active_count"])
            remaining = target_per_owner - active
            items.append({**row, "target_capacity": target_per_owner,
                          "remaining_capacity": remaining,
                          "capacity_status": "OVERLOADED" if remaining < 0 else "NEAR_LIMIT" if remaining <= 3 else "AVAILABLE"})
        return sorted(items, key=lambda item: (item["remaining_capacity"], item["owner"]))

    def contact_window(self, task_id: str, *, local_hour: int) -> dict[str, Any]:
        """Check whether a task is inside a conservative customer contact window."""
        if local_hour < 0 or local_hour > 23:
            raise ValueError("local_hour must be between 0 and 23")
        task = self.get(task_id)
        allowed = 9 <= local_hour < 20
        next_hour = local_hour if allowed else 9
        return {"task_id": task_id, "risk_band": task["risk_band"], "local_hour": local_hour,
                "contact_allowed": allowed, "recommended_start_hour": next_hour,
                "reason": "within contact window" if allowed else "outside 09:00-20:00 contact window"}

    def queue_forecast(self, *, handling_rate_per_hour: int, horizon_hours: int = 8) -> dict[str, Any]:
        """Forecast whether active tasks fit inside the available handling horizon."""
        if handling_rate_per_hour < 1 or horizon_hours < 1 or horizon_hours > 168:
            raise ValueError("handling rate and horizon must be positive")
        active = len(self.priority_queue(limit=500))
        capacity = handling_rate_per_hour * horizon_hours
        remaining = max(0, active - capacity)
        return {"active_tasks": active, "handling_rate_per_hour": handling_rate_per_hour,
                "horizon_hours": horizon_hours, "capacity": capacity,
                "forecast_remaining": remaining,
                "status": "CAPACITY_GAP" if remaining else "ON_TRACK"}

    def strategy_guardrails(self, *, minimum_observations: int = 30) -> list[dict[str, Any]]:
        """Mark strategies that have enough mature evidence for comparison."""
        if minimum_observations < 1:
            raise ValueError("minimum_observations must be positive")
        items = []
        for row in self.strategy_effectiveness():
            mature = int(row["observed_30d_count"])
            items.append({**row, "minimum_observations": minimum_observations,
                          "decision_ready": mature >= minimum_observations,
                          "decision_status": "READY" if mature >= minimum_observations else "COLLECT_MORE_DATA"})
        return items

    def daily_briefing(self, *, handling_rate_per_hour: int = 5, horizon_hours: int = 8) -> dict[str, Any]:
        """Combine queue, SLA and capacity signals for a shift handoff."""
        forecast = self.queue_forecast(handling_rate_per_hour=handling_rate_per_hour, horizon_hours=horizon_hours)
        sla = self.task_sla_summary()
        workloads = self.owner_workloads()
        actions = []
        if sla["overdue_count"]:
            actions.append("Prioritize overdue retention tasks")
        if forecast["status"] == "CAPACITY_GAP":
            actions.append("Rebalance owners or reduce queue intake")
        if not actions:
            actions.append("Continue with the current priority queue")
        return {"forecast": forecast, "sla": sla, "owner_count": len(workloads),
                "actions": actions,
                "status": "ACTION_REQUIRED" if sla["overdue_count"] or forecast["status"] == "CAPACITY_GAP" else "ON_TRACK"}

    @staticmethod
    def _serialize_outcome(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["retained_7d"] = None if item["retained_7d"] is None else bool(item["retained_7d"])
        item["retained_30d"] = (
            None if item["retained_30d"] is None else bool(item["retained_30d"])
        )
        return item

