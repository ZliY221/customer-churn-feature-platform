"""Create a champion/challenger model comparison report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .comparison import write_model_comparison


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare churn champion and challenger models")
    parser.add_argument("--champion-predictions", type=Path, required=True)
    parser.add_argument("--challenger-predictions", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--performance-policy", type=Path, required=True)
    parser.add_argument("--comparison-policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = write_model_comparison(
        args.champion_predictions,
        args.challenger_predictions,
        args.labels,
        args.performance_policy,
        args.comparison_policy,
        args.output,
    )
    print(json.dumps({"status": report["status"], "recommendation": report["recommendation"]}))
    if report["status"] != "COMPLETE":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
