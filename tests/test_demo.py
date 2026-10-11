from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from churn_platform.demo_cli import run_demo


class DemoPipelineTests(unittest.TestCase):
    def test_demo_builds_every_public_pipeline_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            summary = run_demo(root)

            self.assertEqual(summary["status"], "COMPLETE")
            self.assertEqual(summary["snapshot_count"], 6)
            self.assertEqual(summary["training_row_count"], 600)
            self.assertEqual(summary["reference_prediction_count"], 100)
            self.assertEqual(summary["candidate_prediction_count"], 100)
            for relative_path in (
                "events/customer_events.ndjson",
                "features/training_snapshots.ndjson",
                "model/baseline.json",
                "model/metrics.json",
                "predictions/reference.ndjson",
                "predictions/candidate.ndjson",
                "drift/report.json",
                "summary.json",
            ):
                self.assertTrue((root / relative_path).is_file(), relative_path)

            saved_summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_summary, summary)


if __name__ == "__main__":
    unittest.main()
