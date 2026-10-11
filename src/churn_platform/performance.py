"""Evaluate delayed churn labels against an immutable prediction batch."""

from __future__ import annotations

from datetime import timedelta
import json
import math
import os
from pathlib import Path
import re
from typing import Any

from .features import parse_timestamp
from .inference import canonical_sha256
from .training import classification_metrics


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _identity(row: dict[str, Any], row_type: str, index: int) -> tuple[str, str]:
    customer_key = row.get("customer_key")
    as_of_time = row.get("as_of_time")
    if not isinstance(customer_key, str) or not customer_key:
        raise ValueError(f"{row_type} row {index} has an invalid customer_key")
    if not isinstance(as_of_time, str):
        raise ValueError(f"{row_type} row {index} has an invalid as_of_time")
    parse_timestamp(as_of_time)
    return customer_key, as_of_time


def _validate_policy(policy: dict[str, Any]) -> dict[str, float | int]:
    thresholds = policy.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("performance policy thresholds are required")
    required = {
        "minimum_label_coverage",
        "minimum_labeled_rows",
        "minimum_roc_auc",
        "minimum_pr_auc",
        "minimum_f1",
        "maximum_brier_score",
    }
    if missing := required - thresholds.keys():
        raise ValueError(f"performance policy is missing {sorted(missing)}")
    minimum_rows = thresholds["minimum_labeled_rows"]
    if isinstance(minimum_rows, bool) or not isinstance(minimum_rows, int) or minimum_rows < 1:
        raise ValueError("minimum_labeled_rows must be a positive integer")
    values: dict[str, float | int] = {"minimum_labeled_rows": minimum_rows}
    for name in required - {"minimum_labeled_rows"}:
        value = float(thresholds[name])
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be between 0 and 1")
        values[name] = value
    return values


def evaluate_performance(
    predictions: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Join delayed labels by exact identity and emit aggregate performance evidence."""
    limits = _validate_policy(policy)
    if not predictions:
        raise ValueError("prediction batch must not be empty")
    prediction_index: dict[tuple[str, str], tuple[float, float]] = {}
    model_hashes: set[str] = set()
    thresholds: set[float] = set()
    for index, row in enumerate(predictions, start=1):
        identity = _identity(row, "prediction", index)
        if identity in prediction_index:
            raise ValueError("prediction identities must be unique")
        model_sha256 = row.get("model_sha256")
        if not isinstance(model_sha256, str) or not SHA256_PATTERN.fullmatch(model_sha256):
            raise ValueError(f"prediction row {index} has an invalid model_sha256")
        probability = float(row.get("churn_probability", "nan"))
        threshold = float(row.get("decision_threshold", "nan"))
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("churn_probability must be between 0 and 1")
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("decision_threshold must be between 0 and 1")
        declared = row.get("churn_prediction")
        if not isinstance(declared, bool) or declared != (probability >= threshold):
            raise ValueError("churn_prediction does not match probability and threshold")
        prediction_index[identity] = (probability, threshold)
        model_hashes.add(model_sha256)
        thresholds.add(threshold)
    if len(model_hashes) != 1 or len(thresholds) != 1:
        raise ValueError("prediction batch must use one model and one decision threshold")

    label_index: dict[tuple[str, str], int] = {}
    for index, row in enumerate(labels, start=1):
        identity = _identity(row, "label", index)
        if identity in label_index:
            raise ValueError("label identities must be unique")
        label = row.get("churned_30d")
        if not isinstance(label, bool):
            raise ValueError(f"label row {index} churned_30d must be boolean")
        observed_at = row.get("label_observed_at")
        if not isinstance(observed_at, str):
            raise ValueError(f"label row {index} has an invalid label_observed_at")
        if parse_timestamp(observed_at) < parse_timestamp(identity[1]) + timedelta(days=30):
            raise ValueError("label_observed_at must cover the 30-day outcome window")
        label_index[identity] = int(label)

    matched = sorted(set(prediction_index) & set(label_index))
    matched_labels = [label_index[identity] for identity in matched]
    probabilities = [prediction_index[identity][0] for identity in matched]
    coverage = len(matched) / len(predictions)
    row_counts = {
        "predictions": len(predictions),
        "labels": len(labels),
        "matched": len(matched),
        "unmatched_predictions": len(predictions) - len(matched),
        "unmatched_labels": len(labels) - len(matched),
    }
    readiness_checks = [
        {
            "rule_id": "PERFORMANCE-LABEL-COVERAGE",
            "observed": round(coverage, 6),
            "operator": ">=",
            "threshold": limits["minimum_label_coverage"],
            "status": "PASS" if coverage >= limits["minimum_label_coverage"] else "INSUFFICIENT",
        },
        {
            "rule_id": "PERFORMANCE-LABELED-ROWS",
            "observed": len(matched),
            "operator": ">=",
            "threshold": limits["minimum_labeled_rows"],
            "status": "PASS" if len(matched) >= limits["minimum_labeled_rows"] else "INSUFFICIENT",
        },
        {
            "rule_id": "PERFORMANCE-LABEL-CLASSES",
            "observed": len(set(matched_labels)),
            "operator": "==",
            "threshold": 2,
            "status": "PASS" if len(set(matched_labels)) == 2 else "INSUFFICIENT",
        },
    ]
    insufficient = [row for row in readiness_checks if row["status"] != "PASS"]
    base = {
        "report_version": "1.0",
        "policy_version": str(policy.get("policy_version", "unknown")),
        "model_sha256": next(iter(model_hashes)),
        "lineage": {
            "prediction_sha256": canonical_sha256(predictions),
            "label_sha256": canonical_sha256(labels),
        },
        "row_counts": row_counts,
        "label_coverage": round(coverage, 6),
        "readiness_checks": readiness_checks,
    }
    if insufficient:
        return {
            **base,
            "status": "INSUFFICIENT_DATA",
            "metrics": None,
            "checks": [],
            "alerts": [
                {"alert_id": row["rule_id"], "severity": "warning", **row}
                for row in insufficient
            ],
        }

    threshold = next(iter(thresholds))
    metrics = classification_metrics(matched_labels, probabilities, threshold)
    check_definitions = (
        ("PERFORMANCE-ROC-AUC", "roc_auc", ">=", "minimum_roc_auc"),
        ("PERFORMANCE-PR-AUC", "pr_auc", ">=", "minimum_pr_auc"),
        ("PERFORMANCE-F1", "f1", ">=", "minimum_f1"),
        ("PERFORMANCE-BRIER", "brier_score", "<=", "maximum_brier_score"),
    )
    checks: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    for rule_id, metric_name, operator, policy_name in check_definitions:
        observed = float(metrics[metric_name])
        limit = float(limits[policy_name])
        passed = observed >= limit if operator == ">=" else observed <= limit
        check = {
            "rule_id": rule_id,
            "metric": metric_name,
            "observed": observed,
            "operator": operator,
            "threshold": limit,
            "status": "PASS" if passed else "ALERT",
        }
        checks.append(check)
        if not passed:
            alerts.append({"alert_id": rule_id, "severity": "critical", **check})
    return {
        **base,
        "status": "PASS" if not alerts else "ALERT",
        "metrics": metrics,
        "checks": checks,
        "alerts": alerts,
    }


def _load_ndjson(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"row {line_number} in {path.name} must be an object")
            rows.append(row)
    return rows


def write_performance_report(
    predictions_path: Path,
    labels_path: Path,
    policy_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    report = evaluate_performance(
        _load_ndjson(predictions_path),
        _load_ndjson(labels_path),
        json.loads(policy_path.read_text(encoding="utf-8")),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return report
