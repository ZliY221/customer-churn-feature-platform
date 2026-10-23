"""Cross-campaign contact conflict detection and deterministic resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CampaignEnrollment:
    customer_key: str
    campaign_id: str
    priority: int
    scheduled_hour: int


def resolve_campaign_conflicts(enrollments: list[CampaignEnrollment], *,
                               cooldown_hours: int = 24) -> dict[str, Any]:
    """Select one contact when campaign schedules collide; no contact is sent."""
    if not enrollments or cooldown_hours < 1 or cooldown_hours > 168:
        raise ValueError("enrollments and a valid cooldown are required")
    keys = [(item.customer_key, item.campaign_id) for item in enrollments]
    if len(keys) != len(set(keys)):
        raise ValueError("customer and campaign enrollment must be unique")
    for item in enrollments:
        if (not item.customer_key.strip() or item.customer_key != item.customer_key.strip()
                or not item.campaign_id.strip() or item.campaign_id != item.campaign_id.strip()
                or not 1 <= item.priority <= 100 or item.scheduled_hour < 0):
            raise ValueError("campaign enrollment is invalid")
    decisions = []
    conflict_count = 0
    for customer in sorted({item.customer_key for item in enrollments}):
        rows = sorted((item for item in enrollments if item.customer_key == customer),
                      key=lambda row: (-row.priority, row.scheduled_hour, row.campaign_id))
        accepted: list[CampaignEnrollment] = []
        for item in rows:
            collision = next((winner for winner in accepted
                              if abs(item.scheduled_hour - winner.scheduled_hour) < cooldown_hours), None)
            if collision:
                conflict_count += 1
                decisions.append({"customer_key": customer, "campaign_id": item.campaign_id,
                                  "decision": "DEFER", "conflicts_with": collision.campaign_id,
                                  "reason": "higher-priority contact occupies cooldown window"})
            else:
                accepted.append(item)
                decisions.append({"customer_key": customer, "campaign_id": item.campaign_id,
                                  "decision": "KEEP", "conflicts_with": None,
                                  "reason": "no higher-priority contact conflicts"})
    return {"status": "CONFLICTS_RESOLVED" if conflict_count else "CLEAR",
            "enrollment_count": len(enrollments), "conflict_count": conflict_count,
            "cooldown_hours": cooldown_hours, "decisions": decisions,
            "automatic_contact_started": False}
