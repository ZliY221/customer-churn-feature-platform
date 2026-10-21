"""Customer-level orchestration for retention campaign launches."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from math import ceil
from typing import Any


@dataclass(frozen=True)
class CampaignCandidate:
    customer_key: str
    risk_score: float
    contact_allowed: bool
    owner_available: bool = True


def orchestrate_campaign(campaign_id: str, candidates: list[CampaignCandidate], *,
                         holdout_ratio: float = 0.2,
                         treatment_capacity: int = 500) -> dict[str, Any]:
    """Protect a deterministic holdout and build a capacity-bounded contact queue."""
    if not campaign_id.strip() or campaign_id != campaign_id.strip() or not candidates:
        raise ValueError("campaign_id and candidates are required")
    if not 0 < holdout_ratio < 0.5 or treatment_capacity < 1:
        raise ValueError("holdout ratio or treatment capacity is invalid")
    keys = [item.customer_key.strip() for item in candidates]
    if (any(not key for key in keys)
            or any(item.customer_key != item.customer_key.strip() for item in candidates)
            or len(set(keys)) != len(keys)):
        raise ValueError("customer keys must be non-empty and unique")
    if any(not 0 <= item.risk_score <= 1 for item in candidates):
        raise ValueError("risk_score must be between 0 and 1")

    eligible = [item for item in candidates if item.contact_allowed]
    holdout_count = min(len(eligible), ceil(len(eligible) * holdout_ratio))
    holdout_keys = {
        item.customer_key for item in sorted(
            eligible,
            key=lambda row: hashlib.sha256(
                f"{campaign_id}:{row.customer_key}".encode("utf-8")
            ).hexdigest(),
        )[:holdout_count]
    }
    treatment = sorted(
        [item for item in eligible if item.customer_key not in holdout_keys],
        key=lambda item: (-item.risk_score, item.customer_key),
    )
    prioritized_ready_keys = set(
        [item.customer_key for item in treatment if item.owner_available][:treatment_capacity]
    )
    assignments: list[dict[str, Any]] = []
    for item in sorted(candidates, key=lambda row: row.customer_key):
        if not item.contact_allowed:
            stage, reason = "EXCLUDED", "contact policy does not allow outreach"
        elif item.customer_key in holdout_keys:
            stage, reason = "HOLDOUT", "reserved for campaign effect measurement"
        elif not item.owner_available:
            stage, reason = "DEFERRED_OWNER", "no available campaign owner"
        elif item.customer_key in prioritized_ready_keys:
            stage, reason = "CONTACT_READY", "eligible for the prioritized treatment queue"
        else:
            stage, reason = "DEFERRED_CAPACITY", "treatment capacity is exhausted"
        assignments.append({"customer_key": item.customer_key,
                            "risk_score": item.risk_score,
                            "stage": stage, "reason": reason})

    counts = {stage: sum(row["stage"] == stage for row in assignments) for stage in (
        "CONTACT_READY", "HOLDOUT", "EXCLUDED", "DEFERRED_OWNER", "DEFERRED_CAPACITY")}
    status = "READY" if not counts["DEFERRED_OWNER"] and not counts["DEFERRED_CAPACITY"] else (
        "PARTIAL" if counts["CONTACT_READY"] else "BLOCKED"
    )
    return {"campaign_id": campaign_id, "status": status,
            "holdout_ratio": holdout_ratio, "treatment_capacity": treatment_capacity,
            "counts": counts, "assignments": assignments,
            "automatic_contact_started": False}
