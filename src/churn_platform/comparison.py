"""Compare champion and challenger models on the same matured label cohort."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from .features import parse_timestamp
from .inference import canonical_sha256
from .performance import evaluate_performance


def _identity_set(rows: list[dict[str, Any]], name: str) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=1):
        customer_key = row.get("customer_key")
        as_of_time = row.get("as_of_time")
        if not isinstance(customer_key, str) or not customer_key or not isinstance(as_of_time, str):
            raise ValueError(f"{name} row {index} has an invalid identity")
        parse_timestamp(as_of_time)
        identity = (customer_key, as_of_time)
        if identity in identities:
            raise ValueError(f"{name} identities must be unique")
        identities.add(identity)
    return identities


def _comparison_limits(policy: dict[str, Any]) -> dict[str, float]:
    thresholds = policy.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("model comparison policy thresholds are required")
    required = {
        "minimum_roc_auc_improvement",
        "minimum_pr_auc_improvement",
        "minimum_f1_improvement",
        "maximum_brier_score_regression",
    }
    if missing := required - thresholds.keys():
        raise ValueError(f"model comparison policy is missing {sorted(missing)}")
    limits = {name: float(thresholds[name]) for name in required}
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in limits.values()):
        raise ValueError("model comparison thresholds must be between 0 and 1")
    return limits


def compare_models(
    champion_predictions: list[dict[str, Any]],
    challenger_predictions: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    performance_policy: dict[str, Any],
    comparison_policy: dict[str, Any],
) -> dict[str, Any]:
    """Return an aggregate promotion recommendation for two aligned prediction batches."""
    limits = _comparison_limits(comparison_policy)
    champion_identities = _identity_set(champion_predictions, "champion prediction")
    challenger_identities = _identity_set(challenger_predictions, "challenger prediction")
    if champion_identities != challenger_identities:
        raise ValueError("champion and challenger prediction identities must match exactly")
    champion = evaluate_performance(champion_predictions, labels, performance_policy)
    challenger = evaluate_performance(challenger_predictions, labels, performance_policy)
    champion_model = champion["model_sha256"]
    challenger_model = challenger["model_sha256"]
    if champion_model == challenger_model:
        raise ValueError("champion and challenger must use different model hashes")
    base = {
        "comparison_version": "1.0",
        "policy_version": str(comparison_policy.get("policy_version", "unknown")),
        "lineage": {
            "champion_prediction_sha256": canonical_sha256(champion_predictions),
            "challenger_prediction_sha256": canonical_sha256(challenger_predictions),
            "label_sha256": canonical_sha256(labels),
        },
        "models": {
            "champion_sha256": champion_model,
            "challenger_sha256": challenger_model,
        },
        "row_count": len(champion_predictions),
        "performance_status": {
            "champion": champion["status"],
            "challenger": challenger["status"],
        },
    }
    if champion["status"] == "INSUFFICIENT_DATA" or challenger["status"] == "INSUFFICIENT_DATA":
        return {
            **base,
            "status": "INSUFFICIENT_DATA",
            "recommendation": "DEFER_DECISION",
            "selected_model_sha256": champion_model,
            "metrics": None,
            "deltas": None,
            "checks": [],
        }

    champion_metrics = champion["metrics"]
    challenger_metrics = challenger["metrics"]
    deltas = {
        metric: round(float(challenger_metrics[metric]) - float(champion_metrics[metric]), 6)
        for metric in ("roc_auc", "pr_auc", "f1", "brier_score")
    }
    definitions = (
        (
            "MODEL-COMPARISON-ROC-AUC",
            "roc_auc",
            deltas["roc_auc"],
            ">=",
            limits["minimum_roc_auc_improvement"],
        ),
        (
            "MODEL-COMPARISON-PR-AUC",
            "pr_auc",
            deltas["pr_auc"],
            ">=",
            limits["minimum_pr_auc_improvement"],
        ),
        (
            "MODEL-COMPARISON-F1",
            "f1",
            deltas["f1"],
            ">=",
            limits["minimum_f1_improvement"],
        ),
        (
            "MODEL-COMPARISON-BRIER",
            "brier_score",
            deltas["brier_score"],
            "<=",
            limits["maximum_brier_score_regression"],
        ),
    )
    checks: list[dict[str, Any]] = []
    for rule_id, metric, observed, operator, threshold in definitions:
        passed = observed >= threshold if operator == ">=" else observed <= threshold
        checks.append(
            {
                "rule_id": rule_id,
                "metric": metric,
                "observed_delta": observed,
                "operator": operator,
                "threshold": threshold,
                "status": "PASS" if passed else "FAIL",
            }
        )
    checks.append(
        {
            "rule_id": "MODEL-COMPARISON-CHALLENGER-POLICY",
            "observed": challenger["status"],
            "operator": "==",
            "threshold": "PASS",
            "status": "PASS" if challenger["status"] == "PASS" else "FAIL",
        }
    )
    promote = all(check["status"] == "PASS" for check in checks)
    return {
        **base,
        "status": "COMPLETE",
        "recommendation": "PROMOTE_CHALLENGER" if promote else "KEEP_CHAMPION",
        "selected_model_sha256": challenger_model if promote else champion_model,
        "metrics": {
            "champion": champion_metrics,
            "challenger": challenger_metrics,
        },
        "deltas": deltas,
        "checks": checks,
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


def write_model_comparison(
    champion_path: Path,
    challenger_path: Path,
    labels_path: Path,
    performance_policy_path: Path,
    comparison_policy_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    report = compare_models(
        _load_ndjson(champion_path),
        _load_ndjson(challenger_path),
        _load_ndjson(labels_path),
        json.loads(performance_policy_path.read_text(encoding="utf-8")),
        json.loads(comparison_policy_path.read_text(encoding="utf-8")),
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
