"""Train and evaluate the deterministic temporal baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .features import parse_timestamp
from .training import TemporalSplitConfig, load_snapshot_rows, temporal_split, train_baseline


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a time-split customer churn baseline")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--train-end", required=True)
    parser.add_argument("--validation-end", required=True)
    parser.add_argument("--model-output", type=Path, required=True)
    parser.add_argument("--metrics-output", type=Path, required=True)
    args = parser.parse_args()

    splits = temporal_split(
        load_snapshot_rows(args.input),
        TemporalSplitConfig(
            train_end=parse_timestamp(args.train_end),
            validation_end=parse_timestamp(args.validation_end),
        ),
    )
    model, metrics = train_baseline(splits)
    for path, document in ((args.model_output, model), (args.metrics_output, metrics)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"trained {model['algorithm']}; validation_f1={metrics['validation']['f1']}; "
        f"test_roc_auc={metrics['test']['roc_auc']}"
    )


if __name__ == "__main__":
    main()
