"""Segment-level fairness audit for planned campaign delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeliveryAssignment:
    customer_key: str
    segment: str
    stage: str
    channel: str | None = None


def audit_campaign_delivery(campaign_id: str, assignments: list[DeliveryAssignment],
                            *, maximum_rate_gap: float = 0.2) -> dict[str, Any]:
    """Compare routing completion rates without initiating campaign delivery."""
    if (not campaign_id.strip() or campaign_id != campaign_id.strip()
            or not assignments or not 0 <= maximum_rate_gap <= 1):
        raise ValueError("campaign id, assignments and a valid maximum gap are required")
    keys = [item.customer_key.strip() for item in assignments]
    if (any(not key for key in keys) or len(keys) != len(set(keys))
            or any(item.customer_key != item.customer_key.strip() for item in assignments)):
        raise ValueError("customer keys must be non-empty and unique")
    for item in assignments:
        if (not item.segment.strip() or item.segment != item.segment.strip()
                or item.stage not in {"ROUTED", "DEFERRED_CHANNEL"}
                or (item.stage == "ROUTED" and not item.channel)):
            raise ValueError("delivery assignment is invalid")

    rows = []
    for segment in sorted({item.segment for item in assignments}):
        members = [item for item in assignments if item.segment == segment]
        routed = sum(item.stage == "ROUTED" for item in members)
        channels: dict[str, int] = {}
        for item in members:
            if item.channel:
                channels[item.channel] = channels.get(item.channel, 0) + 1
        rows.append({"segment": segment, "customer_count": len(members),
                     "routed_count": routed, "deferred_count": len(members) - routed,
                     "routing_rate": routed / len(members), "channel_counts": channels})
    rates = [row["routing_rate"] for row in rows]
    gap = max(rates) - min(rates)
    return {"campaign_id": campaign_id,
            "status": "REVIEW" if gap > maximum_rate_gap else "BALANCED",
            "maximum_rate_gap": maximum_rate_gap, "observed_rate_gap": gap,
            "segments": rows, "automatic_delivery_started": False}
