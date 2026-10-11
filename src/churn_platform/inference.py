"""Idempotent batch inference with model and dataset lineage."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable

from .training import FEATURE_COLUMNS


def canonical_sha256(document: Any) -> str:
    payload = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_model(path: Path) -> dict[str, Any]:
    model = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(model, dict):
        raise ValueError("model artifact must be a JSON object")
    if model.get("feature_columns") != list(FEATURE_COLUMNS):
        raise ValueError("model feature order does not match the inference contract")
    expected_length = len(FEATURE_COLUMNS)
    for field in ("means", "scales", "weights"):
        values = model.get(field)
        if not isinstance(values, list) or len(values) != expected_length:
            raise ValueError(f"model {field} must contain {expected_length} values")
    threshold = model.get("decision_threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        raise ValueError("model decision_threshold must be between 0 and 1")
    if any(float(scale) <= 0 for scale in model["scales"]):
        raise ValueError("model scales must be positive")
    return model


def predict_rows(
    rows: Iterable[dict[str, Any]], model: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    model_sha256 = canonical_sha256(model)
    predictions: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for row in rows:
        missing = {*FEATURE_COLUMNS, "customer_key", "as_of_time"} - row.keys()
        if missing:
            raise ValueError(f"feature row is missing {sorted(missing)}")
        identity = (str(row["customer_key"]), str(row["as_of_time"]))
        if identity in identities:
            raise ValueError(f"duplicate inference identity: {identity[0]} at {identity[1]}")
        identities.add(identity)
        values = [float(row[column]) for column in FEATURE_COLUMNS]
        standardized = [
            (value - float(model["means"][index])) / float(model["scales"][index])
            for index, value in enumerate(values)
        ]
        score = float(model["intercept"]) + sum(
            float(weight) * value for weight, value in zip(model["weights"], standardized)
        )
        probability = 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, score))))
        identity_material = f"{model_sha256}:{identity[0]}:{identity[1]}".encode("utf-8")
        predictions.append(
            {
                "schema_version": 1,
                "prediction_id": f"pred_{hashlib.sha256(identity_material).hexdigest()[:32]}",
                "customer_key": identity[0],
                "as_of_time": identity[1],
                "model_sha256": model_sha256,
                "churn_probability": f"{probability:.6f}",
                "churn_prediction": probability >= float(model["decision_threshold"]),
                "decision_threshold": f"{float(model['decision_threshold']):.6f}",
            }
        )
    predictions.sort(key=lambda row: (str(row["as_of_time"]), str(row["customer_key"])))
    return predictions, model_sha256


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_prediction_batch(
    predictions: list[dict[str, Any]],
    output_path: Path,
    manifest_path: Path,
    *,
    model_sha256: str,
    input_sha256: str,
) -> dict[str, Any]:
    lines = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in predictions]
    content = "\n".join(lines) + ("\n" if lines else "")
    output_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    manifest = {
        "manifest_version": "1.0",
        "status": "COMPLETE",
        "input_sha256": input_sha256,
        "model_sha256": model_sha256,
        "output_sha256": output_sha256,
        "prediction_count": len(predictions),
        "output_name": output_path.name,
    }
    _atomic_write(output_path, content)
    _atomic_write(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    return manifest
