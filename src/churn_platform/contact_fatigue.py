"""Protect customers from excessive or poorly timed retention contacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ContactFatigueCandidate:
    customer_key: str
    contacts_last_7d: int
    contacts_last_30d: int
    hours_since_last_contact: int
    negative_responses_last_30d: int
    contact_allowed: bool
    preferred_channel: str


def assess_contact_fatigue(
    candidates: list[ContactFatigueCandidate],
    *,
    maximum_contacts_7d: int = 2,
    maximum_contacts_30d: int = 6,
    cooldown_hours: int = 48,
    negative_response_limit: int = 2,
) -> dict[str, Any]:
    """Return customer-safe contact eligibility without sending a message."""
    if not candidates:
        raise ValueError("at least one candidate is required")
    if min(maximum_contacts_7d, maximum_contacts_30d, cooldown_hours, negative_response_limit) < 1:
        raise ValueError("fatigue thresholds must be positive")
    keys = [item.customer_key.strip() for item in candidates]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("customer keys must be non-empty and unique")

    results = []
    for item in candidates:
        counts = (
            item.contacts_last_7d,
            item.contacts_last_30d,
            item.hours_since_last_contact,
            item.negative_responses_last_30d,
        )
        if any(value < 0 for value in counts) or item.contacts_last_7d > item.contacts_last_30d:
            raise ValueError("contact history is invalid")
        if not item.preferred_channel.strip():
            raise ValueError("preferred channel is required")

        reasons = []
        if not item.contact_allowed:
            status, action = "PROTECTED", "DO_NOT_CONTACT"
            reasons.append("CONTACT_NOT_ALLOWED")
        elif item.negative_responses_last_30d >= negative_response_limit:
            status, action = "REVIEW", "REVIEW_CUSTOMER_FEEDBACK"
            reasons.append("NEGATIVE_RESPONSE_LIMIT_REACHED")
        elif item.hours_since_last_contact < cooldown_hours:
            status, action = "COOLDOWN", "WAIT_FOR_COOLDOWN"
            reasons.append("COOLDOWN_ACTIVE")
        elif item.contacts_last_7d >= maximum_contacts_7d:
            status, action = "COOLDOWN", "WAIT_FOR_WEEKLY_WINDOW"
            reasons.append("WEEKLY_CONTACT_LIMIT_REACHED")
        elif item.contacts_last_30d >= maximum_contacts_30d:
            status, action = "COOLDOWN", "WAIT_FOR_MONTHLY_WINDOW"
            reasons.append("MONTHLY_CONTACT_LIMIT_REACHED")
        else:
            status, action = "ELIGIBLE", "ALLOW_MANUAL_CONTACT_REVIEW"

        remaining_cooldown = max(cooldown_hours - item.hours_since_last_contact, 0)
        results.append({
            **asdict(item),
            "customer_key": item.customer_key.strip(),
            "preferred_channel": item.preferred_channel.strip().upper(),
            "status": status,
            "recommended_action": action,
            "reasons": reasons,
            "remaining_cooldown_hours": remaining_cooldown,
            "contact_eligible": status == "ELIGIBLE",
        })

    counts = {
        status.lower(): sum(item["status"] == status for item in results)
        for status in ("ELIGIBLE", "COOLDOWN", "REVIEW", "PROTECTED")
    }
    return {
        "status": "CONTACT_FATIGUE_ASSESSED",
        "candidate_count": len(results),
        "counts": counts,
        "customers": results,
        "automatic_contact_started": False,
    }
