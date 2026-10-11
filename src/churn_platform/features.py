"""Point-in-time customer feature snapshots and future churn labels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import Iterable

from .generator import EVENT_TYPES
from .validation import validate_event


@dataclass(frozen=True)
class SnapshotConfig:
    as_of_time: datetime
    observation_days: int = 30
    label_days: int = 30

    def validate(self) -> None:
        if self.as_of_time.tzinfo is None:
            raise ValueError("as_of_time must include a timezone")
        if self.observation_days < 1:
            raise ValueError("observation_days must be greater than zero")
        if self.label_days < 1:
            raise ValueError("label_days must be greater than zero")


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and normalize it to UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def load_events(path: Path) -> list[dict[str, object]]:
    """Load and contract-check an NDJSON event file."""
    events: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON at line {line_number}") from error
            if not isinstance(event, dict):
                raise ValueError(f"event at line {line_number} must be an object")
            validate_event(event)
            events.append(event)
    return events


def build_feature_snapshot(
    events: Iterable[dict[str, object]], config: SnapshotConfig
) -> list[dict[str, object]]:
    """Build deterministic features and labels with strict point-in-time boundaries."""
    config.validate()
    as_of = config.as_of_time.astimezone(UTC)
    observation_start = as_of - timedelta(days=config.observation_days)
    label_end = as_of + timedelta(days=config.label_days)

    parsed_events: list[tuple[dict[str, object], datetime]] = []
    for event in events:
        validate_event(event)
        parsed_events.append((event, parse_timestamp(str(event["event_time"]))))

    customers = sorted(
        {
            str(event["customer_key"])
            for event, event_time in parsed_events
            if event_time <= as_of
        }
    )
    rows: list[dict[str, object]] = []
    for customer_key in customers:
        history = [
            (event, event_time)
            for event, event_time in parsed_events
            if event["customer_key"] == customer_key
            and observation_start < event_time <= as_of
        ]
        prior_events = [
            event_time
            for event, event_time in parsed_events
            if event["customer_key"] == customer_key and event_time <= as_of
        ]
        future_events = [
            event_time
            for event, event_time in parsed_events
            if event["customer_key"] == customer_key and as_of < event_time <= label_end
        ]
        counts = {
            event_type: sum(event["event_type"] == event_type for event, _ in history)
            for event_type in EVENT_TYPES
        }
        events_7d = sum(event_time > as_of - timedelta(days=7) for _, event_time in history)
        revenue = sum(
            (
                Decimal(str(event["order_amount"]))
                for event, _ in history
                if event["event_type"] == "order_completed"
            ),
            start=Decimal("0.00"),
        )
        cart_count = counts["cart_add"]
        cart_to_order_rate = (
            Decimal(counts["order_completed"]) / Decimal(cart_count)
            if cart_count
            else Decimal("0")
        )
        last_event_time = max(prior_events)
        rows.append(
            {
                "schema_version": 1,
                "customer_key": customer_key,
                "as_of_time": as_of.isoformat().replace("+00:00", "Z"),
                "observation_days": config.observation_days,
                "label_days": config.label_days,
                "events_7d": events_7d,
                "events_30d": len(history),
                "active_days_30d": len({event_time.date() for _, event_time in history}),
                "login_count_30d": counts["login"],
                "product_view_count_30d": counts["product_view"],
                "cart_add_count_30d": cart_count,
                "order_count_30d": counts["order_completed"],
                "support_ticket_count_30d": counts["support_ticket"],
                "revenue_30d": str(revenue.quantize(Decimal("0.01"))),
                "cart_to_order_rate_30d": str(cart_to_order_rate.quantize(Decimal("0.0001"))),
                "days_since_last_event": (as_of.date() - last_event_time.date()).days,
                "churned_30d": not future_events,
            }
        )
    return rows


def write_snapshot(rows: Iterable[dict[str, object]], path: Path) -> None:
    """Write a stable NDJSON snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
