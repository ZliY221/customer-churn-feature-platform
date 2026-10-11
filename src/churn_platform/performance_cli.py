"""Create an aggregate delayed-label model performance report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .performance import write_performance_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor delayed-label churn performance")
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = write_performance_report(args.predictions, args.labels, args.policy, args.output)
    print(json.dumps({"status": report["status"], "alerts": len(report["alerts"])}))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
