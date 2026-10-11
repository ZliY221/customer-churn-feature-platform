"""Deterministic temporal splitting and logistic-regression baseline training."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from .features import parse_timestamp

FEATURE_COLUMNS = (
    "events_7d",
    "events_30d",
    "active_days_30d",
    "login_count_30d",
    "product_view_count_30d",
    "cart_add_count_30d",
    "order_count_30d",
    "support_ticket_count_30d",
    "revenue_30d",
    "days_since_last_event",
)


@dataclass(frozen=True)
class TemporalSplitConfig:
    train_end: datetime
    validation_end: datetime

    def validate(self) -> None:
        if self.train_end.tzinfo is None or self.validation_end.tzinfo is None:
            raise ValueError("split timestamps must include a timezone")
        if self.train_end >= self.validation_end:
            raise ValueError("train_end must be earlier than validation_end")


def load_snapshot_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"snapshot row {line_number} must be an object")
            missing = {*FEATURE_COLUMNS, "customer_key", "as_of_time", "churned_30d"} - row.keys()
            if missing:
                raise ValueError(f"snapshot row {line_number} is missing {sorted(missing)}")
            rows.append(row)
    return rows


def temporal_split(
    rows: Iterable[dict[str, Any]], config: TemporalSplitConfig
) -> dict[str, list[dict[str, Any]]]:
    """Split by snapshot time so later observations never enter earlier datasets."""
    config.validate()
    train_end = config.train_end.astimezone(config.train_end.tzinfo)
    validation_end = config.validation_end.astimezone(config.validation_end.tzinfo)
    splits = {"train": [], "validation": [], "test": []}
    for row in rows:
        as_of = parse_timestamp(str(row["as_of_time"]))
        if as_of <= train_end:
            target = "train"
        elif as_of <= validation_end:
            target = "validation"
        else:
            target = "test"
        splits[target].append(row)
    for name, values in splits.items():
        if not values:
            raise ValueError(f"{name} split must not be empty")
        values.sort(key=lambda row: (str(row["as_of_time"]), str(row["customer_key"])))
    return splits


def _matrix(rows: list[dict[str, Any]]) -> tuple[list[list[float]], list[int]]:
    matrix = [[float(row[column]) for column in FEATURE_COLUMNS] for row in rows]
    labels = [int(bool(row["churned_30d"])) for row in rows]
    return matrix, labels


def _sigmoid(value: float) -> float:
    bounded = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-bounded))


def _transform(matrix: list[list[float]], means: list[float], scales: list[float]) -> list[list[float]]:
    return [
        [(value - means[index]) / scales[index] for index, value in enumerate(row)]
        for row in matrix
    ]


def _probabilities(matrix: list[list[float]], weights: list[float], intercept: float) -> list[float]:
    return [_sigmoid(intercept + sum(w * x for w, x in zip(weights, row))) for row in matrix]


def _roc_auc(labels: list[int], probabilities: list[float]) -> float:
    positives = [score for label, score in zip(labels, probabilities) if label]
    negatives = [score for label, score in zip(labels, probabilities) if not label]
    if not positives or not negatives:
        raise ValueError("ROC-AUC requires both label classes")
    wins = sum(
        1.0 if positive > negative else 0.5 if positive == negative else 0.0
        for positive in positives
        for negative in negatives
    )
    return wins / (len(positives) * len(negatives))


def _average_precision(labels: list[int], probabilities: list[float]) -> float:
    ranked = sorted(zip(probabilities, labels), key=lambda pair: pair[0], reverse=True)
    positive_count = sum(labels)
    if positive_count == 0:
        raise ValueError("PR-AUC requires positive labels")
    true_positives = 0
    precision_sum = 0.0
    for rank, (_, label) in enumerate(ranked, start=1):
        if label:
            true_positives += 1
            precision_sum += true_positives / rank
    return precision_sum / positive_count


def classification_metrics(
    labels: list[int], probabilities: list[float], threshold: float
) -> dict[str, Any]:
    predictions = [int(probability >= threshold) for probability in probabilities]
    tp = sum(label == 1 and prediction == 1 for label, prediction in zip(labels, predictions))
    fp = sum(label == 0 and prediction == 1 for label, prediction in zip(labels, predictions))
    tn = sum(label == 0 and prediction == 0 for label, prediction in zip(labels, predictions))
    fn = sum(label == 1 and prediction == 0 for label, prediction in zip(labels, predictions))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "row_count": len(labels),
        "positive_count": sum(labels),
        "threshold": round(threshold, 6),
        "roc_auc": round(_roc_auc(labels, probabilities), 6),
        "pr_auc": round(_average_precision(labels, probabilities), 6),
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(2 * precision * recall / (precision + recall), 6) if precision + recall else 0.0,
        "brier_score": round(
            sum((probability - label) ** 2 for probability, label in zip(probabilities, labels))
            / len(labels),
            6,
        ),
        "confusion_matrix": {"true_positive": tp, "false_positive": fp, "true_negative": tn, "false_negative": fn},
    }


def train_baseline(
    splits: dict[str, list[dict[str, Any]]], *, epochs: int = 500, learning_rate: float = 0.05
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit on train, choose the threshold on validation, and evaluate test once."""
    if epochs < 1 or learning_rate <= 0:
        raise ValueError("epochs and learning_rate must be positive")
    train_x, train_y = _matrix(splits["train"])
    if len(set(train_y)) != 2:
        raise ValueError("training split must contain both label classes")
    means = [sum(row[index] for row in train_x) / len(train_x) for index in range(len(FEATURE_COLUMNS))]
    scales = []
    for index, mean in enumerate(means):
        variance = sum((row[index] - mean) ** 2 for row in train_x) / len(train_x)
        scales.append(math.sqrt(variance) or 1.0)
    standardized = _transform(train_x, means, scales)
    weights = [0.0] * len(FEATURE_COLUMNS)
    intercept = 0.0
    for _ in range(epochs):
        probabilities = _probabilities(standardized, weights, intercept)
        errors = [probability - label for probability, label in zip(probabilities, train_y)]
        intercept -= learning_rate * sum(errors) / len(errors)
        for index in range(len(weights)):
            gradient = sum(error * row[index] for error, row in zip(errors, standardized)) / len(errors)
            weights[index] -= learning_rate * (gradient + 0.001 * weights[index])

    validation_x, validation_y = _matrix(splits["validation"])
    validation_probabilities = _probabilities(_transform(validation_x, means, scales), weights, intercept)
    thresholds = sorted({0.5, *[round(value, 6) for value in validation_probabilities]})
    threshold = max(
        thresholds,
        key=lambda value: (classification_metrics(validation_y, validation_probabilities, value)["f1"], -abs(value - 0.5)),
    )
    metrics: dict[str, Any] = {}
    for name, rows in splits.items():
        matrix, labels = _matrix(rows)
        probabilities = _probabilities(_transform(matrix, means, scales), weights, intercept)
        metrics[name] = classification_metrics(labels, probabilities, threshold)
    fingerprint = hashlib.sha256(
        json.dumps(splits, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    model = {
        "model_version": "1.0",
        "algorithm": "logistic_regression_gradient_descent",
        "feature_columns": list(FEATURE_COLUMNS),
        "means": [round(value, 12) for value in means],
        "scales": [round(value, 12) for value in scales],
        "weights": [round(value, 12) for value in weights],
        "intercept": round(intercept, 12),
        "decision_threshold": threshold,
        "epochs": epochs,
        "learning_rate": learning_rate,
        "training_data_sha256": fingerprint,
    }
    return model, metrics
