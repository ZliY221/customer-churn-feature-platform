#!/usr/bin/env python3
"""Run the complete deterministic churn platform demonstration with one command."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = str(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])


def _output_path(value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (ROOT / value).resolve()


def _run(label: str, *arguments: str) -> None:
    print(f"==> {label}", flush=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"{label} failed with exit code {completed.returncode}")


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the complete deterministic customer churn flow"
    )
    parser.add_argument("--output-dir", type=Path, default=Path("build/quickstart"))
    args = parser.parse_args()
    output = _output_path(args.output_dir)
    demo_output = output / "demo"
    doctor_json = output / "doctor.json"
    doctor_markdown = output / "doctor.md"

    _run(
        "Inspect environment",
        "scripts/project_doctor.py",
        "--profile",
        "core",
        "--json-output",
        str(doctor_json),
        "--markdown-output",
        str(doctor_markdown),
    )
    _run(
        "Build features, models, predictions and monitoring reports",
        "-m",
        "churn_platform.demo_cli",
        "--output-dir",
        str(demo_output),
    )
    demo_summary_path = demo_output / "summary.json"
    demo_summary = json.loads(demo_summary_path.read_text(encoding="utf-8"))
    if demo_summary.get("status") != "COMPLETE":
        raise SystemExit("demo verification failed: completion status is missing")

    summary = {
        "status": "COMPLETE",
        "project": "customer-churn-feature-platform",
        "version": VERSION,
        "event_count": demo_summary["event_count"],
        "training_row_count": demo_summary["training_row_count"],
        "model_sha256": demo_summary["model_sha256"],
        "drift_status": demo_summary["drift_status"],
        "performance_status": demo_summary["performance_status"],
        "model_recommendation": demo_summary["model_recommendation"],
        "outputs": {
            "doctor": str(doctor_json),
            "demo_summary": str(demo_summary_path),
            **demo_summary["outputs"],
        },
    }
    summary_path = output / "summary.json"
    _write_json_atomic(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
