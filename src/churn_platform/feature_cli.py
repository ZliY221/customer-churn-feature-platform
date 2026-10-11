"""Command line entry point for point-in-time feature snapshots."""

from __future__ import annotations

import argparse
from pathlib import Path

from .features import SnapshotConfig, build_feature_snapshot, load_events, parse_timestamp, write_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a point-in-time customer feature snapshot")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of-time", required=True)
    parser.add_argument("--observation-days", type=int, default=30)
    parser.add_argument("--label-days", type=int, default=30)
    args = parser.parse_args()

    config = SnapshotConfig(
        as_of_time=parse_timestamp(args.as_of_time),
        observation_days=args.observation_days,
        label_days=args.label_days,
    )
    rows = build_feature_snapshot(load_events(args.input), config)
    write_snapshot(rows, args.output)
    churned = sum(bool(row["churned_30d"]) for row in rows)
    print(f"wrote {len(rows)} customer rows; churned={churned}; active={len(rows) - churned}")


if __name__ == "__main__":
    main()
