"""Capacity-aware channel routing for retention campaign candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChannelCapacity:
    channel: str
    capacity: int
    expected_response_rate: float


@dataclass(frozen=True)
class ChannelCandidate:
    customer_key: str
    risk_score: float
    preferred_channels: tuple[str, ...]


def route_campaign_channels(campaign_id: str, candidates: list[ChannelCandidate],
                            channels: list[ChannelCapacity]) -> dict[str, Any]:
    """Route customers to preferred channels without initiating contact."""
    if not campaign_id.strip() or campaign_id != campaign_id.strip() or not candidates or not channels:
        raise ValueError("campaign id, candidates and channels are required")
    keys = [item.customer_key.strip() for item in candidates]
    channel_names = [item.channel.strip().upper() for item in channels]
    if (any(not key for key in keys) or len(keys) != len(set(keys))
            or any(item.customer_key != item.customer_key.strip() for item in candidates)):
        raise ValueError("customer keys must be non-empty and unique")
    if (any(not name for name in channel_names) or len(channel_names) != len(set(channel_names))
            or any(item.channel != item.channel.strip().upper() for item in channels)):
        raise ValueError("channel names must be uppercase and unique")
    channel_by_name = {item.channel: item for item in channels}
    for item in channels:
        if item.capacity < 0 or not 0 <= item.expected_response_rate <= 1:
            raise ValueError("channel capacity or response rate is invalid")
    for item in candidates:
        if (not 0 <= item.risk_score <= 1 or not item.preferred_channels
                or len(item.preferred_channels) != len(set(item.preferred_channels))
                or any(channel != channel.strip().upper() for channel in item.preferred_channels)):
            raise ValueError("candidate channel preferences are invalid")

    remaining = {item.channel: item.capacity for item in channels}
    assignments = []
    for item in sorted(candidates, key=lambda row: (-row.risk_score, row.customer_key)):
        available = [channel_by_name[name] for name in item.preferred_channels
                     if name in channel_by_name and remaining[name] > 0]
        available.sort(key=lambda row: (-row.expected_response_rate,
                                        item.preferred_channels.index(row.channel)))
        if available:
            selected = available[0]
            remaining[selected.channel] -= 1
            assignments.append({"customer_key": item.customer_key,
                                "risk_score": item.risk_score,
                                "channel": selected.channel,
                                "stage": "ROUTED",
                                "reason": "preferred channel with available capacity"})
        else:
            assignments.append({"customer_key": item.customer_key,
                                "risk_score": item.risk_score, "channel": None,
                                "stage": "DEFERRED_CHANNEL",
                                "reason": "no preferred channel has remaining capacity"})
    routed = sum(row["stage"] == "ROUTED" for row in assignments)
    return {"campaign_id": campaign_id,
            "status": "READY" if routed == len(assignments) else "PARTIAL" if routed else "BLOCKED",
            "routed_count": routed,
            "deferred_count": len(assignments) - routed,
            "remaining_capacity": remaining, "assignments": assignments,
            "automatic_contact_started": False}
