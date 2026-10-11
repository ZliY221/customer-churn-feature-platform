"""Run an idempotent batch inference job."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .inference import canonical_sha256, load_model, predict_rows, write_prediction_batch
from .training import load_snapshot_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic churn batch inference")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    args = parser.parse_args()

    rows = load_snapshot_rows(args.input)
    model = load_model(args.model)
    predictions, model_sha256 = predict_rows(rows, model)
    manifest = write_prediction_batch(
        predictions,
        args.output,
        args.manifest_output,
        model_sha256=model_sha256,
        input_sha256=canonical_sha256(rows),
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
