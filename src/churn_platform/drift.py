"""Aggregate feature and prediction drift monitoring without customer-level output."""

from __future__ import annotations

from bisect import bisect_right
import json
import math
import os
from pathlib import Path
from typing import Any

from .inference import canonical_sha256
from .training import FEATURE_COLUMNS


def load_ndjson(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"row {line_number} in {path.name} must be an object")
            rows.append(row)
    if not rows:
        raise ValueError(f"{path.name} must not be empty")
    return rows


def _finite_feature_values(rows: list[dict[str, Any]], feature: str) -> list[float]:
    values: list[float] = []
    for index, row in enumerate(rows, start=1):
        if feature not in row:
            raise ValueError(f"feature row {index} is missing {feature}")
        value = float(row[feature])
        if not math.isfinite(value):
            raise ValueError(f"feature {feature} must contain finite values")
        values.append(value)
    return values


def population_stability_index(reference: list[float], candidate: list[float]) -> float:
    if not reference or not candidate:
        raise ValueError("PSI samples must not be empty")
    ordered = sorted(reference)
    if ordered[0] == ordered[-1]:
        anchor = ordered[0]

        def locate(value: float) -> int:
            return 0 if value < anchor else 1 if value == anchor else 2

        bin_count = 3
    else:
        boundaries = sorted(
            {
                ordered[min(len(ordered) - 1, math.ceil(q * len(ordered) / 10) - 1)]
                for q in range(1, 10)
            }
        )

        def locate(value: float) -> int:
            return bisect_right(boundaries, value)

        bin_count = len(boundaries) + 1
    reference_counts = [0] * bin_count
    candidate_counts = [0] * bin_count
    for value in reference:
        reference_counts[locate(value)] += 1
    for value in candidate:
        candidate_counts[locate(value)] += 1
    epsilon = 1e-6
    reference_total = len(reference) + epsilon * bin_count
    candidate_total = len(candidate) + epsilon * bin_count
    score = 0.0
    for reference_count, candidate_count in zip(reference_counts, candidate_counts):
        reference_ratio = (reference_count + epsilon) / reference_total
        candidate_ratio = (candidate_count + epsilon) / candidate_total
        score += (candidate_ratio - reference_ratio) * math.log(candidate_ratio / reference_ratio)
    return round(score, 6)


def _prediction_summary(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    probabilities: list[float] = []
    positives = 0
    for index, row in enumerate(rows, start=1):
        if "churn_probability" not in row or "churn_prediction" not in row:
            raise ValueError(f"prediction row {index} is missing required fields")
        probability = float(row["churn_probability"])
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("churn_probability must be between 0 and 1")
        prediction = row["churn_prediction"]
        if not isinstance(prediction, bool):
            raise ValueError("churn_prediction must be boolean")
        probabilities.append(probability)
        positives += int(prediction)
    if not probabilities:
        raise ValueError("prediction rows must not be empty")
    return {
        "row_count": len(probabilities),
        "mean_probability": round(sum(probabilities) / len(probabilities), 6),
        "positive_rate": round(positives / len(probabilities), 6),
    }


def monitor_drift(
    reference_features: list[dict[str, Any]],
    candidate_features: list[dict[str, Any]],
    reference_predictions: list[dict[str, Any]],
    candidate_predictions: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    thresholds = policy.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("drift policy thresholds are required")
    required = {"max_feature_psi", "max_mean_probability_shift", "max_positive_rate_shift"}
    if missing := required - thresholds.keys():
        raise ValueError(f"drift policy is missing {sorted(missing)}")
    limits = {name: float(thresholds[name]) for name in required}
    if any(not math.isfinite(value) or value < 0 for value in limits.values()):
        raise ValueError("drift thresholds must be finite and non-negative")

    alerts: list[dict[str, Any]] = []
    feature_metrics: list[dict[str, Any]] = []
    for feature in FEATURE_COLUMNS:
        score = population_stability_index(
            _finite_feature_values(reference_features, feature),
            _finite_feature_values(candidate_features, feature),
        )
        passed = score <= limits["max_feature_psi"]
        metric = {
            "feature": feature,
            "psi": score,
            "threshold": limits["max_feature_psi"],
            "status": "PASS" if passed else "ALERT",
        }
        feature_metrics.append(metric)
        if not passed:
            alerts.append(
                {
                    "alert_id": f"FEATURE-PSI:{feature}",
                    "severity": "warning",
                    **metric,
                }
            )

    reference_prediction = _prediction_summary(reference_predictions)
    candidate_prediction = _prediction_summary(candidate_predictions)
    prediction_checks = [
        (
            "PREDICTION-MEAN-SHIFT",
            abs(
                float(candidate_prediction["mean_probability"])
                - float(reference_prediction["mean_probability"])
            ),
            limits["max_mean_probability_shift"],
        ),
        (
            "PREDICTION-POSITIVE-RATE-SHIFT",
            abs(
                float(candidate_prediction["positive_rate"])
                - float(reference_prediction["positive_rate"])
            ),
            limits["max_positive_rate_shift"],
        ),
    ]
    prediction_metrics: list[dict[str, Any]] = []
    for rule_id, observed, threshold in prediction_checks:
        metric = {
            "rule_id": rule_id,
            "absolute_shift": round(observed, 6),
            "threshold": threshold,
            "status": "PASS" if observed <= threshold else "ALERT",
        }
        prediction_metrics.append(metric)
        if metric["status"] == "ALERT":
            alerts.append({"alert_id": rule_id, "severity": "critical", **metric})
    return {
        "report_version": "1.0",
        "status": "PASS" if not alerts else "ALERT",
        "policy_version": str(policy.get("policy_version", "unknown")),
        "lineage": {
            "reference_feature_sha256": canonical_sha256(reference_features),
            "candidate_feature_sha256": canonical_sha256(candidate_features),
            "reference_prediction_sha256": canonical_sha256(reference_predictions),
            "candidate_prediction_sha256": canonical_sha256(candidate_predictions),
        },
        "row_counts": {
            "reference_features": len(reference_features),
            "candidate_features": len(candidate_features),
            "reference_predictions": len(reference_predictions),
            "candidate_predictions": len(candidate_predictions),
        },
        "feature_drift": feature_metrics,
        "prediction_drift": {
            "reference": reference_prediction,
            "candidate": candidate_prediction,
            "checks": prediction_metrics,
        },
        "alerts": alerts,
    }


def write_drift_report(
    reference_features_path: Path,
    candidate_features_path: Path,
    reference_predictions_path: Path,
    candidate_predictions_path: Path,
    policy_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    report = monitor_drift(
        load_ndjson(reference_features_path),
        load_ndjson(candidate_features_path),
        load_ndjson(reference_predictions_path),
        load_ndjson(candidate_predictions_path),
        json.loads(policy_path.read_text(encoding="utf-8")),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return report
