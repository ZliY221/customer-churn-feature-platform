"""Command line entry point for synthetic event generation."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

from .generator import GeneratorConfig, generate_events
from .validation import validate_event


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic customer behavior events")
    parser.add_argument("--customers", type=int, default=100)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--seed", type=int, default=2027)
    parser.add_argument("--start-time", default="2026-01-01T00:00:00Z")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    start_time = datetime.fromisoformat(args.start_time.replace("Z", "+00:00"))
    events = generate_events(
        GeneratorConfig(
            customer_count=args.customers,
            days=args.days,
            seed=args.seed,
            start_time=start_time,
        )
    )
    for event in events:
        validate_event(event)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    print(f"generated {len(events)} events for {args.customers} synthetic customers")


if __name__ == "__main__":
    main()
