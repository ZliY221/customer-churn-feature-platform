"""Prioritize retention tasks that need ownership or SLA intervention."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RetentionTaskSla:
    task_id: str
    risk_band: str
    status: str
    owner: str | None
    created_at: datetime
    due_at: datetime
    last_activity_at: datetime


def plan_task_escalations(
    tasks: list[RetentionTaskSla],
    *,
    now: datetime,
    due_soon_minutes: int = 120,
    inactivity_minutes: int = 240,
    focus_limit: int = 20,
) -> dict[str, Any]:
    """Return a bounded, read-only SLA queue for retention operators."""
    if not tasks or now.tzinfo is None:
        raise ValueError("tasks and timezone-aware now are required")
    if due_soon_minutes < 1 or inactivity_minutes < 1 or not 1 <= focus_limit <= 100:
        raise ValueError("SLA thresholds and focus limit must be valid")
    ids = [item.task_id.strip() for item in tasks]
    if any(not item_id for item_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("task ids must be non-empty and unique")

    active = []
    risk_order = {"CRITICAL": 0, "HIGH": 1, "WATCH": 2, "STABLE": 3}
    action_order = {
        "ESCALATE_OVERDUE": 0,
        "ASSIGN_OWNER": 1,
        "REENGAGE_INACTIVE": 2,
        "PRIORITIZE_DUE_SOON": 3,
        "MONITOR": 4,
    }
    for task in tasks:
        if task.risk_band not in risk_order:
            raise ValueError("unsupported risk band")
        if task.status not in {"OPEN", "IN_PROGRESS", "COMPLETED", "DISMISSED"}:
            raise ValueError("unsupported task status")
        if any(value.tzinfo is None for value in (task.created_at, task.due_at, task.last_activity_at)):
            raise ValueError("task timestamps must include a timezone")
        if task.due_at < task.created_at or task.last_activity_at < task.created_at:
            raise ValueError("task timeline is invalid")
        if task.created_at > now or task.last_activity_at > now:
            raise ValueError("task activity must not be in the future")
        if task.status in {"COMPLETED", "DISMISSED"}:
            continue
        minutes_to_due = int((task.due_at - now).total_seconds() // 60)
        inactive_for = max(0, int((now - task.last_activity_at).total_seconds() // 60))
        if minutes_to_due < 0:
            action = "ESCALATE_OVERDUE"
            reason = "TASK_SLA_OVERDUE"
        elif not (task.owner or "").strip():
            action = "ASSIGN_OWNER"
            reason = "TASK_OWNER_MISSING"
        elif inactive_for >= inactivity_minutes:
            action = "REENGAGE_INACTIVE"
            reason = "TASK_ACTIVITY_STALE"
        elif minutes_to_due <= due_soon_minutes:
            action = "PRIORITIZE_DUE_SOON"
            reason = "TASK_DUE_SOON"
        else:
            action = "MONITOR"
            reason = "TASK_WITHIN_SLA"
        active.append({
            "task_id": task.task_id.strip(),
            "risk_band": task.risk_band,
            "status": task.status,
            "owner": (task.owner or "").strip() or None,
            "minutes_to_due": minutes_to_due,
            "inactive_minutes": inactive_for,
            "recommended_action": action,
            "reason": reason,
            "requires_confirmation": action != "MONITOR",
        })

    active.sort(key=lambda item: (
        action_order[item["recommended_action"]],
        risk_order[item["risk_band"]],
        item["minutes_to_due"],
        item["task_id"],
    ))
    counts = {
        action.lower(): sum(item["recommended_action"] == action for item in active)
        for action in action_order
    }
    focus = active[:focus_limit]
    actionable = len(active) - counts["monitor"]
    return {
        "status": "SLA_ATTENTION_REQUIRED" if actionable else "SLA_ON_TRACK",
        "active_task_count": len(active),
        "actionable_count": actionable,
        "visible_count": len(focus),
        "remaining_count": max(0, len(active) - len(focus)),
        "counts": counts,
        "focus_items": focus,
        "automatic_task_changes_started": False,
    }
