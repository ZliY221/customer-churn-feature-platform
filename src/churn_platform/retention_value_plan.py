"""Plan retention offers using consent, uplift confidence, value, and budget."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RetentionValueCandidate:
    customer_key: str
    risk_score: float
    expected_uplift: float
    uplift_uncertainty: float
    customer_value: float
    offer_cost: float
    contact_allowed: bool
    channel: str


def plan_retention_value(
    campaign_id: str,
    candidates: list[RetentionValueCandidate],
    *,
    campaign_budget: float,
    maximum_offers: int,
    minimum_adjusted_uplift: float = 0.02,
) -> dict[str, Any]:
    """Select economically useful offers without starting customer contact."""
    if not campaign_id.strip() or not candidates:
        raise ValueError("campaign id and candidates are required")
    if campaign_budget < 0 or maximum_offers < 1 or not 0 <= minimum_adjusted_uplift <= 1:
        raise ValueError("campaign constraints are invalid")
    keys = [item.customer_key.strip() for item in candidates]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("customer keys must be non-empty and unique")

    evaluated = []
    for item in candidates:
        values = (item.risk_score, item.expected_uplift, item.uplift_uncertainty)
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("risk, uplift, and uncertainty must be between 0 and 1")
        if item.customer_value < 0 or item.offer_cost < 0 or not item.channel.strip():
            raise ValueError("candidate value, cost, and channel are invalid")
        adjusted_uplift = max(item.expected_uplift - item.uplift_uncertainty, 0)
        expected_saved_value = item.customer_value * adjusted_uplift
        expected_net_value = expected_saved_value - item.offer_cost
        score = item.risk_score * max(expected_net_value, 0)
        eligible = (
            item.contact_allowed
            and adjusted_uplift >= minimum_adjusted_uplift
            and expected_net_value > 0
        )
        reasons = []
        if not item.contact_allowed:
            reasons.append("CONTACT_NOT_ALLOWED")
        if adjusted_uplift < minimum_adjusted_uplift:
            reasons.append("UPLIFT_BELOW_THRESHOLD")
        if expected_net_value <= 0:
            reasons.append("NON_POSITIVE_EXPECTED_VALUE")
        evaluated.append({
            **asdict(item),
            "customer_key": item.customer_key.strip(),
            "channel": item.channel.strip().upper(),
            "adjusted_uplift": round(adjusted_uplift, 4),
            "expected_saved_value": round(expected_saved_value, 2),
            "expected_net_value": round(expected_net_value, 2),
            "priority_score": round(score, 4),
            "eligible": eligible,
            "exclusion_reasons": reasons,
        })

    selected = []
    deferred = []
    spent = 0.0
    for row in sorted(evaluated, key=lambda value: (-value["priority_score"], value["customer_key"])):
        if not row["eligible"]:
            deferred.append({**row, "decision": "EXCLUDED"})
        elif len(selected) >= maximum_offers:
            deferred.append({**row, "decision": "DEFERRED_CAPACITY"})
        elif spent + row["offer_cost"] > campaign_budget:
            deferred.append({**row, "decision": "DEFERRED_BUDGET"})
        else:
            spent += row["offer_cost"]
            selected.append({**row, "decision": "PLANNED"})

    return {
        "status": "RETENTION_VALUE_PLAN_READY",
        "campaign_id": campaign_id.strip(),
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "deferred_count": len(deferred),
        "campaign_budget": round(campaign_budget, 2),
        "planned_cost": round(spent, 2),
        "remaining_budget": round(campaign_budget - spent, 2),
        "expected_net_value": round(sum(row["expected_net_value"] for row in selected), 2),
        "selected": selected,
        "deferred": deferred,
        "automatic_contact_started": False,
    }
