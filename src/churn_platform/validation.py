"""Dependency-free validation for generated behavior events."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from .generator import CHANNELS, DEVICE_TYPES, EVENT_TYPES, PRODUCT_CATEGORIES

EVENT_ID_PATTERN = re.compile(r"^evt_[0-9a-f]{32}$")
CUSTOMER_KEY_PATTERN = re.compile(r"^cus_[0-9a-f]{20}$")
REQUIRED_FIELDS = {
    "schema_version",
    "event_id",
    "customer_key",
    "event_type",
    "event_time",
    "ingest_time",
    "channel",
    "device_type",
    "product_category",
    "order_amount",
}


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def validate_event(event: dict[str, Any]) -> None:
    missing = REQUIRED_FIELDS - event.keys()
    extra = event.keys() - REQUIRED_FIELDS
    if missing or extra:
        raise ValueError(f"event fields mismatch: missing={sorted(missing)}, extra={sorted(extra)}")
    if event["schema_version"] != 1:
        raise ValueError("schema_version must equal 1")
    if not isinstance(event["event_id"], str) or not EVENT_ID_PATTERN.fullmatch(
        event["event_id"]
    ):
        raise ValueError("event_id has an invalid format")
    if not isinstance(event["customer_key"], str) or not CUSTOMER_KEY_PATTERN.fullmatch(
        event["customer_key"]
    ):
        raise ValueError("customer_key has an invalid format")
    if event["event_type"] not in EVENT_TYPES:
        raise ValueError("event_type is unsupported")
    if event["channel"] not in CHANNELS:
        raise ValueError("channel is unsupported")
    if event["device_type"] not in DEVICE_TYPES:
        raise ValueError("device_type is unsupported")
    if event["product_category"] not in PRODUCT_CATEGORIES:
        raise ValueError("product_category is unsupported")
    event_time = _timestamp(event["event_time"], "event_time")
    ingest_time = _timestamp(event["ingest_time"], "ingest_time")
    if ingest_time < event_time:
        raise ValueError("ingest_time must not be earlier than event_time")
    if ingest_time - event_time > timedelta(minutes=5):
        raise ValueError("ingest delay must not exceed five minutes")
    amount = event["order_amount"]
    if event["event_type"] == "order_completed":
        try:
            parsed_amount = Decimal(str(amount))
        except (InvalidOperation, TypeError) as error:
            raise ValueError("completed orders require a decimal order_amount") from error
        if parsed_amount <= 0 or parsed_amount.as_tuple().exponent != -2:
            raise ValueError("order_amount must be positive with two decimal places")
    elif amount is not None:
        raise ValueError("non-order events must not contain order_amount")
