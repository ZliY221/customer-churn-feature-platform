from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from churn_platform.inference import (
    canonical_sha256,
    load_model,
    predict_rows,
    write_prediction_batch,
)
from churn_platform.training import FEATURE_COLUMNS


def _model() -> dict[str, object]:
    return {
        "model_version": "1.0",
        "algorithm": "logistic_regression_gradient_descent",
        "feature_columns": list(FEATURE_COLUMNS),
        "means": [0.0] * len(FEATURE_COLUMNS),
        "scales": [1.0] * len(FEATURE_COLUMNS),
        "weights": [0.1] * len(FEATURE_COLUMNS),
        "intercept": -1.0,
        "decision_threshold": 0.5,
    }


def _row(customer: str = "cus_00000000000000000000") -> dict[str, object]:
    row: dict[str, object] = {
        "customer_key": customer,
        "as_of_time": "2026-03-31T00:00:00Z",
    }
    row.update({column: "10.00" if column == "revenue_30d" else 1 for column in FEATURE_COLUMNS})
    return row


class BatchInferenceTests(unittest.TestCase):
    def test_predictions_have_stable_ids_and_model_lineage(self) -> None:
        first, first_hash = predict_rows([_row()], _model())
        second, second_hash = predict_rows([_row()], _model())

        self.assertEqual(first, second)
        self.assertEqual(first_hash, second_hash)
        self.assertTrue(first[0]["prediction_id"].startswith("pred_"))
        self.assertEqual(first_hash, first[0]["model_sha256"])

    def test_batch_outputs_are_byte_identical_on_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions, model_hash = predict_rows([_row()], _model())
            first = write_prediction_batch(
                predictions,
                root / "predictions.ndjson",
                root / "manifest.json",
                model_sha256=model_hash,
                input_sha256=canonical_sha256([_row()]),
            )
            output_bytes = (root / "predictions.ndjson").read_bytes()
            manifest_bytes = (root / "manifest.json").read_bytes()
            second = write_prediction_batch(
                predictions,
                root / "predictions.ndjson",
                root / "manifest.json",
                model_sha256=model_hash,
                input_sha256=canonical_sha256([_row()]),
            )

            self.assertEqual(first, second)
            self.assertEqual(output_bytes, (root / "predictions.ndjson").read_bytes())
            self.assertEqual(manifest_bytes, (root / "manifest.json").read_bytes())
            self.assertEqual(first["output_sha256"], hashlib.sha256(output_bytes).hexdigest())

    def test_duplicate_identity_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate inference identity"):
            predict_rows([_row(), _row()], _model())

    def test_model_contract_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.json"
            invalid = _model()
            invalid["feature_columns"] = list(reversed(FEATURE_COLUMNS))
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "feature order"):
                load_model(path)


if __name__ == "__main__":
    unittest.main()
