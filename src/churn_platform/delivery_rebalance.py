"""Capacity-bounded remediation plan for campaign delivery gaps."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any


@dataclass(frozen=True)
class SegmentDeliveryState:
    segment: str
    customer_count: int
    routed_count: int
    reserve_candidates: int


def plan_delivery_rebalance(campaign_id: str, segments: list[SegmentDeliveryState], *,
                            reserve_capacity: int, target_rate_gap: float = 0.1) -> dict[str, Any]:
    """Recommend extra routes for under-served segments without changing assignments."""
    if (not campaign_id.strip() or campaign_id != campaign_id.strip() or not segments
            or reserve_capacity < 0 or not 0 <= target_rate_gap <= 1):
        raise ValueError("campaign, segment and capacity inputs are invalid")
    names = [item.segment.strip() for item in segments]
    if (any(not name for name in names) or len(names) != len(set(names))
            or any(item.segment != item.segment.strip() for item in segments)):
        raise ValueError("segment names must be non-empty and unique")
    for item in segments:
        if (item.customer_count < 1 or item.routed_count < 0
                or item.routed_count > item.customer_count or item.reserve_candidates < 0
                or item.reserve_candidates > item.customer_count - item.routed_count):
            raise ValueError("segment delivery state is invalid")

    best_rate = max(item.routed_count / item.customer_count for item in segments)
    target_rate = max(0.0, best_rate - target_rate_gap)
    remaining = reserve_capacity
    actions = []
    total_required = 0
    for item in sorted(segments, key=lambda row: (row.routed_count / row.customer_count,
                                                   row.segment)):
        required = max(0, ceil(target_rate * item.customer_count - 1e-12)
                       - item.routed_count)
        total_required += required
        recommended = min(required, item.reserve_candidates, remaining)
        remaining -= recommended
        projected = (item.routed_count + recommended) / item.customer_count
        actions.append({"segment": item.segment, "current_routing_rate":
                        item.routed_count / item.customer_count,
                        "target_routing_rate": target_rate,
                        "required_additional_routes": required,
                        "recommended_additional_routes": recommended,
                        "projected_routing_rate": projected,
                        "unresolved_routes": required - recommended})
    unresolved = sum(row["unresolved_routes"] for row in actions)
    status = ("BALANCED" if total_required == 0 else
              "READY" if unresolved == 0 else
              "PARTIAL" if reserve_capacity > remaining else "NO_CAPACITY")
    return {"campaign_id": campaign_id, "status": status,
            "target_rate_gap": target_rate_gap, "target_routing_rate": target_rate,
            "reserve_capacity": reserve_capacity, "used_capacity": reserve_capacity - remaining,
            "remaining_capacity": remaining, "actions": actions,
            "automatic_rerouting_started": False}
