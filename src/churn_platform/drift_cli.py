"""Create an aggregate feature and prediction drift report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .drift import write_drift_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor churn feature and prediction drift")
    parser.add_argument("--reference-features", type=Path, required=True)
    parser.add_argument("--candidate-features", type=Path, required=True)
    parser.add_argument("--reference-predictions", type=Path, required=True)
    parser.add_argument("--candidate-predictions", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = write_drift_report(
        args.reference_features,
        args.candidate_features,
        args.reference_predictions,
        args.candidate_predictions,
        args.policy,
        args.output,
    )
    print(json.dumps({"status": report["status"], "alerts": len(report["alerts"])}))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
