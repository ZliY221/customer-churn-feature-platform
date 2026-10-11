"""Build a complete, deterministic local demonstration of the churn pipeline."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from .drift import monitor_drift
from .features import SnapshotConfig, build_feature_snapshot, write_snapshot
from .generator import GeneratorConfig, generate_events
from .inference import canonical_sha256, predict_rows, write_prediction_batch
from .performance import evaluate_performance
from .training import TemporalSplitConfig, temporal_split, train_baseline
from .validation import validate_event


SNAPSHOT_TIMES = (
    "2026-03-01T00:00:00Z",
    "2026-03-31T00:00:00Z",
    "2026-04-15T00:00:00Z",
    "2026-04-30T00:00:00Z",
    "2026-05-15T00:00:00Z",
    "2026-05-31T00:00:00Z",
)


def _write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_events(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


def run_demo(output_dir: Path) -> dict[str, Any]:
    """Run generation, snapshots, training, inference and drift reporting."""
    events = generate_events(
        GeneratorConfig(
            customer_count=100,
            days=211,
            seed=2027,
            start_time=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    for event in events:
        validate_event(event)
    events_path = output_dir / "events" / "customer_events.ndjson"
    _write_events(events_path, events)

    snapshots: dict[str, list[dict[str, object]]] = {}
    all_rows: list[dict[str, object]] = []
    for timestamp in SNAPSHOT_TIMES:
        as_of = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        rows = build_feature_snapshot(events, SnapshotConfig(as_of_time=as_of))
        date_key = timestamp[:10]
        snapshots[date_key] = rows
        all_rows.extend(rows)
        write_snapshot(rows, output_dir / "snapshots" / f"{date_key}.ndjson")

    training_path = output_dir / "features" / "training_snapshots.ndjson"
    write_snapshot(all_rows, training_path)
    splits = temporal_split(
        all_rows,
        TemporalSplitConfig(
            train_end=datetime(2026, 3, 31, tzinfo=UTC),
            validation_end=datetime(2026, 4, 30, tzinfo=UTC),
        ),
    )
    model, metrics = train_baseline(splits)
    model_path = output_dir / "model" / "baseline.json"
    metrics_path = output_dir / "model" / "metrics.json"
    _write_json(model_path, model)
    _write_json(metrics_path, metrics)

    prediction_sets: dict[str, list[dict[str, Any]]] = {}
    manifests: dict[str, dict[str, Any]] = {}
    model_sha256 = canonical_sha256(model)
    for role, date_key in (("reference", "2026-05-15"), ("candidate", "2026-05-31")):
        rows = snapshots[date_key]
        predictions, observed_model_sha256 = predict_rows(rows, model)
        if observed_model_sha256 != model_sha256:
            raise RuntimeError("model lineage changed during demo inference")
        prediction_sets[role] = predictions
        manifests[role] = write_prediction_batch(
            predictions,
            output_dir / "predictions" / f"{role}.ndjson",
            output_dir / "predictions" / f"{role}-manifest.json",
            model_sha256=model_sha256,
            input_sha256=canonical_sha256(rows),
        )

    config_root = Path(__file__).resolve().parents[2] / "config"
    policy = json.loads((config_root / "drift-policy.json").read_text(encoding="utf-8"))
    drift_report = monitor_drift(
        snapshots["2026-05-15"],
        snapshots["2026-05-31"],
        prediction_sets["reference"],
        prediction_sets["candidate"],
        policy,
    )
    drift_path = output_dir / "drift" / "report.json"
    _write_json(drift_path, drift_report)

    label_rows = [
        {
            "customer_key": row["customer_key"],
            "as_of_time": row["as_of_time"],
            "churned_30d": row["churned_30d"],
            "label_observed_at": "2026-07-01T00:00:00Z",
        }
        for row in snapshots["2026-05-31"]
    ]
    labels_path = output_dir / "labels" / "candidate.ndjson"
    _write_events(labels_path, label_rows)
    performance_policy = json.loads(
        (config_root / "performance-policy.json").read_text(encoding="utf-8")
    )
    performance_report = evaluate_performance(
        prediction_sets["candidate"], label_rows, performance_policy
    )
    performance_path = output_dir / "performance" / "report.json"
    _write_json(performance_path, performance_report)

    summary = {
        "status": "COMPLETE",
        "event_count": len(events),
        "snapshot_count": len(SNAPSHOT_TIMES),
        "training_row_count": len(all_rows),
        "model_sha256": model_sha256,
        "reference_prediction_count": manifests["reference"]["prediction_count"],
        "candidate_prediction_count": manifests["candidate"]["prediction_count"],
        "drift_status": drift_report["status"],
        "drift_alert_count": len(drift_report["alerts"]),
        "performance_status": performance_report["status"],
        "performance_alert_count": len(performance_report["alerts"]),
        "outputs": {
            "events": str(events_path),
            "training_features": str(training_path),
            "model": str(model_path),
            "metrics": str(metrics_path),
            "drift_report": str(drift_path),
            "labels": str(labels_path),
            "performance_report": str(performance_path),
        },
    }
    _write_json(output_dir / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the complete deterministic churn demo")
    parser.add_argument("--output-dir", type=Path, default=Path("build/demo"))
    args = parser.parse_args()
    print(json.dumps(run_demo(args.output_dir), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
