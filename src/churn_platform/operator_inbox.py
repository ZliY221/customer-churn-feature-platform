"""Create a bounded, consent-aware retention operator inbox."""

from __future__ import annotations

from typing import Any

from .operator_journey import RetentionOperatorJourney, rehearse_retention_journeys


STATUS_ORDER = {"PRIORITY": 0, "GUIDED": 1, "MONITOR": 2, "READY": 3, "PROTECTED": 4}
ACTION_MINUTES = {
    "RECORD_CONSENT_RESTRICTION": 3,
    "RESCHEDULE_CONTACT": 6,
    "ASSIGN_MANUAL_REVIEW": 10,
    "PRIORITIZE_RETENTION_TASK": 8,
    "WAIT_FOR_OUTCOME_WINDOW": 2,
    "REVIEW_EFFECTIVENESS": 12,
}


def build_retention_operator_inbox(
    journeys: list[RetentionOperatorJourney], *, visible_limit: int = 15
) -> dict[str, Any]:
    """Prioritize safe work while separating protected and deferred customers."""
    if not 1 <= visible_limit <= 25:
        raise ValueError("visible limit must be between 1 and 25")
    rehearsal = rehearse_retention_journeys(journeys)
    ordered = sorted(
        rehearsal["journeys"],
        key=lambda item: (
            STATUS_ORDER[item["status"]],
            -int(item["requires_confirmation"]),
            item["journey_id"],
        ),
    )
    actionable = [item for item in ordered if item["status"] != "PROTECTED"]
    focus_items = []
    for rank, item in enumerate(actionable[:visible_limit], start=1):
        focus_items.append({
            **item,
            "rank": rank,
            "estimated_minutes": ACTION_MINUTES[item["primary_action"]],
            "resume_key": f"{item['journey_id']}:{item['primary_action']}",
            "contact_allowed": item["primary_action"] not in {
                "ASSIGN_MANUAL_REVIEW", "WAIT_FOR_OUTCOME_WINDOW"
            },
        })
    filters = {
        status.lower(): sum(item["status"] == status for item in ordered)
        for status in STATUS_ORDER
    }
    return {
        "status": "RETENTION_INBOX_READY",
        "journey_count": 100,
        "visible_count": len(focus_items),
        "remaining_actionable_count": len(actionable) - len(focus_items),
        "protected_count": filters["protected"],
        "filters": filters,
        "estimated_focus_minutes": sum(item["estimated_minutes"] for item in focus_items),
        "focus_items": focus_items,
        "guidance": "优先处理逾期任务，联系前再次确认授权、冷却窗口与渠道可用性。",
        "automatic_contact_started": False,
    }
