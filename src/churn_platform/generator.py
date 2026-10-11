"""Deterministic synthetic customer behavior event generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import random
from uuid import NAMESPACE_URL, uuid5

EVENT_TYPES = (
    "login",
    "product_view",
    "cart_add",
    "order_completed",
    "support_ticket",
)
CHANNELS = ("app", "web", "mini_program")
DEVICE_TYPES = ("android", "ios", "desktop")
PRODUCT_CATEGORIES = ("digital", "home", "sports", "beauty")


@dataclass(frozen=True)
class GeneratorConfig:
    customer_count: int = 100
    days: int = 60
    seed: int = 2027
    start_time: datetime = datetime(2026, 1, 1, tzinfo=UTC)

    def validate(self) -> None:
        if self.customer_count < 1:
            raise ValueError("customer_count must be greater than zero")
        if not 2 <= self.days <= 365:
            raise ValueError("days must be between 2 and 365")
        if self.start_time.tzinfo is None:
            raise ValueError("start_time must include a timezone")


def _identifier(kind: str, seed: int, *parts: object) -> str:
    material = ":".join([kind, str(seed), *(str(part) for part in parts)])
    return uuid5(NAMESPACE_URL, material).hex


def _customer_key(seed: int, customer_index: int) -> str:
    return f"cus_{_identifier('customer', seed, customer_index)[:20]}"


def generate_events(config: GeneratorConfig) -> list[dict[str, object]]:
    """Return stable synthetic events ordered by event time and event ID."""
    config.validate()
    start = config.start_time.astimezone(UTC)
    events: list[dict[str, object]] = []

    for customer_index in range(config.customer_count):
        customer_key = _customer_key(config.seed, customer_index)
        customer_rng = random.Random(config.seed * 1_000_003 + customer_index)
        channel = CHANNELS[customer_rng.randrange(len(CHANNELS))]
        device_type = DEVICE_TYPES[customer_rng.randrange(len(DEVICE_TYPES))]
        category = PRODUCT_CATEGORIES[customer_rng.randrange(len(PRODUCT_CATEGORIES))]
        lifecycle = customer_rng.random()
        active_until = config.days
        if lifecycle < 0.32:
            active_until = max(7, int(config.days * customer_rng.uniform(0.35, 0.65)))

        for day_index in range(active_until):
            activity_probability = 0.34 if lifecycle >= 0.32 else 0.24
            if customer_rng.random() > activity_probability:
                continue
            daily_events = 1 + customer_rng.randrange(4)
            for sequence in range(daily_events):
                event_type = customer_rng.choices(
                    EVENT_TYPES,
                    weights=(20, 43, 16, 14, 7),
                    k=1,
                )[0]
                second = customer_rng.randrange(86_400)
                event_time = start + timedelta(days=day_index, seconds=second)
                ingest_delay = customer_rng.randrange(0, 181)
                order_amount = None
                if event_type == "order_completed":
                    order_amount = str(
                        Decimal(customer_rng.randrange(1_999, 59_901)).scaleb(-2).quantize(
                            Decimal("0.01")
                        )
                    )
                events.append(
                    {
                        "schema_version": 1,
                        "event_id": f"evt_{_identifier('event', config.seed, customer_index, day_index, sequence)}",
                        "customer_key": customer_key,
                        "event_type": event_type,
                        "event_time": event_time.isoformat().replace("+00:00", "Z"),
                        "ingest_time": (event_time + timedelta(seconds=ingest_delay))
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "channel": channel,
                        "device_type": device_type,
                        "product_category": category,
                        "order_amount": order_amount,
                    }
                )

    return sorted(events, key=lambda row: (str(row["event_time"]), str(row["event_id"])))
