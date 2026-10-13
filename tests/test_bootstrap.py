from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "churn_bootstrap", ROOT / "scripts" / "bootstrap.py"
)
assert SPEC is not None and SPEC.loader is not None
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOTSTRAP)


class BootstrapTest(unittest.TestCase):
    def test_signature_tracks_project_and_dependency_configuration(self) -> None:
        signature = BOOTSTRAP._runtime_signature()
        self.assertEqual(signature["schema"], 1)
        self.assertEqual(signature["project"], "customer-churn-feature-platform")
        self.assertEqual(signature["project_version"], "0.12.0")
        self.assertEqual(len(signature["checkout_key"]), 12)
        self.assertEqual(signature["install_extras"], "")
        self.assertEqual(len(signature["pyproject_sha256"]), 64)

    def test_runtime_directory_is_restricted_to_a_build_child(self) -> None:
        default = BOOTSTRAP._resolve_runtime_dir(None)
        self.assertTrue(default.is_relative_to(BOOTSTRAP.MANAGED_CACHE_ROOT))
        self.assertIn("customer-churn-feature-platform", default.name)
        expected = (ROOT / "build" / "portable-runtime").resolve()
        self.assertEqual(
            BOOTSTRAP._resolve_runtime_dir(Path("build/portable-runtime")), expected
        )
        with self.assertRaises(ValueError):
            BOOTSTRAP._resolve_runtime_dir(Path("build"))
        with self.assertRaises(ValueError):
            BOOTSTRAP._resolve_runtime_dir(ROOT.parent / "external-runtime")

    def test_marker_round_trip_and_invalid_marker_recovery(self) -> None:
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
            runtime_dir = Path(directory)
            signature = BOOTSTRAP._runtime_signature()
            BOOTSTRAP._write_marker(runtime_dir, signature)
            self.assertEqual(BOOTSTRAP._read_marker(runtime_dir), signature)
            (runtime_dir / BOOTSTRAP.MARKER_NAME).write_text("{", encoding="utf-8")
            self.assertIsNone(BOOTSTRAP._read_marker(runtime_dir))

    def test_portable_workflow_uses_the_public_bootstrap(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "portable-smoke.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count("python scripts/bootstrap.py"), 2)
        self.assertNotIn("python -m pip install", workflow)


if __name__ == "__main__":
    unittest.main()
