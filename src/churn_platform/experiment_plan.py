"""Operational planning for measurable retention campaign experiments."""

from __future__ import annotations

from math import ceil
from typing import Any


def plan_retention_experiment(*, eligible_customers: int, holdout_ratio: float = 0.2,
                              minimum_holdout: int = 100,
                              daily_treatment_capacity: int = 500,
                              campaign_days: int = 5) -> dict[str, Any]:
    """Reserve a holdout and verify treatment capacity before campaign launch."""
    if eligible_customers < 1 or minimum_holdout < 1:
        raise ValueError("customer and holdout counts must be positive")
    if not 0 < holdout_ratio < 0.5:
        raise ValueError("holdout_ratio must be between 0 and 0.5")
    if daily_treatment_capacity < 1 or campaign_days < 1 or campaign_days > 90:
        raise ValueError("campaign capacity is invalid")

    requested_holdout = max(minimum_holdout, ceil(eligible_customers * holdout_ratio))
    holdout_count = min(eligible_customers, requested_holdout)
    treatment_count = eligible_customers - holdout_count
    treatment_capacity = daily_treatment_capacity * campaign_days
    capacity_gap = max(0, treatment_count - treatment_capacity)
    sample_gap = max(0, minimum_holdout - holdout_count)
    if treatment_count < minimum_holdout or sample_gap:
        status = "INSUFFICIENT_SAMPLE"
    elif capacity_gap:
        status = "CAPACITY_GAP"
    else:
        status = "READY"
    return {
        "eligible_customers": eligible_customers,
        "holdout_ratio": holdout_ratio,
        "holdout_count": holdout_count,
        "treatment_count": treatment_count,
        "minimum_holdout": minimum_holdout,
        "daily_treatment_capacity": daily_treatment_capacity,
        "campaign_days": campaign_days,
        "treatment_capacity": treatment_capacity,
        "capacity_gap": capacity_gap,
        "minimum_rollout_days": ceil(treatment_count / daily_treatment_capacity),
        "status": status,
        "holdout_protected": holdout_count > 0,
    }
