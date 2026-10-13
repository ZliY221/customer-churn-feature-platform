"""Portfolio-level risk segmentation for retention operations."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RiskBand(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    WATCH = "WATCH"
    STABLE = "STABLE"


@dataclass(frozen=True)
class RiskPolicy:
    critical_threshold: float = 0.80
    high_threshold: float = 0.60
    watch_threshold: float = 0.40

    def classify(self, probability: float) -> RiskBand:
        if probability >= self.critical_threshold:
            return RiskBand.CRITICAL
        if probability >= self.high_threshold:
            return RiskBand.HIGH
        if probability >= self.watch_threshold:
            return RiskBand.WATCH
        return RiskBand.STABLE


DEFAULT_PLAYBOOKS: dict[RiskBand, str] = {
    RiskBand.CRITICAL: "priority-retention-call",
    RiskBand.HIGH: "targeted-benefit-offer",
    RiskBand.WATCH: "engagement-follow-up",
    RiskBand.STABLE: "routine-care",
}


def portfolio_summary(
    rows: list[dict[str, Any]],
    *,
    policy: RiskPolicy | None = None,
) -> dict[str, Any]:
    if not rows:
        raise ValueError("prediction portfolio must not be empty")
    active_policy = policy or RiskPolicy()
    probabilities = [float(row["churn_probability"]) for row in rows]
    bands = Counter(active_policy.classify(value).value for value in probabilities)
    latest_as_of_time = max(str(row["as_of_time"]) for row in rows)
    return {
        "customer_count": len(rows),
        "predicted_churn_count": sum(bool(row["churn_prediction"]) for row in rows),
        "average_churn_probability": sum(probabilities) / len(probabilities),
        "latest_as_of_time": latest_as_of_time,
        "risk_bands": {band.value: bands[band.value] for band in RiskBand},
    }


def priority_customers(
    rows: list[dict[str, Any]],
    *,
    policy: RiskPolicy | None = None,
    risk_band: RiskBand | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    active_policy = policy or RiskPolicy()
    enriched = []
    for row in rows:
        probability = float(row["churn_probability"])
        band = active_policy.classify(probability)
        if risk_band is not None and band != risk_band:
            continue
        enriched.append(
            {
                **row,
                "risk_band": band.value,
                "recommended_playbook": DEFAULT_PLAYBOOKS[band],
            }
        )
    enriched.sort(
        key=lambda row: (
            -float(row["churn_probability"]),
            str(row["customer_key"]),
            str(row["as_of_time"]),
        )
    )
    return enriched[:limit]

