#!/usr/bin/env python3
"""Cross-platform preflight checks for the customer churn platform."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT = "customer-churn-feature-platform"
MIN_PYTHON = (3, 11)
ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "pyproject.toml",
    "data/sample/customer_events.ndjson",
    "data/sample/customer_features.ndjson",
    "config/drift-policy.json",
    "config/performance-policy.json",
    "config/model-comparison-policy.json",
)


def result(check_id: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def module_result(module: str) -> dict[str, str]:
    available = importlib.util.find_spec(module) is not None
    return result(
        f"module.{module}",
        f"Python module {module}",
        "PASS" if available else "FAIL",
        "available" if available else f"install the project dependency that provides {module}",
    )


def writable_result() -> dict[str, str]:
    build = ROOT / "build"
    try:
        build.mkdir(parents=True, exist_ok=True)
        descriptor, probe = tempfile.mkstemp(prefix="doctor-", dir=build)
        os.close(descriptor)
        Path(probe).unlink()
    except OSError as exc:
        return result("workspace.write", "Build directory", "FAIL", type(exc).__name__)
    return result("workspace.write", "Build directory", "PASS", "build/ is writable")


def evaluate(profile: str) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    python_ok = sys.version_info[:2] >= MIN_PYTHON
    checks.append(
        result(
            "runtime.python",
            "Python runtime",
            "PASS" if python_ok else "FAIL",
            f"{platform.python_version()} (requires {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+)",
        )
    )
    for relative in REQUIRED_FILES:
        exists = (ROOT / relative).is_file()
        checks.append(
            result(
                f"file.{relative}",
                relative,
                "PASS" if exists else "FAIL",
                "present" if exists else "required project file is missing",
            )
        )
    checks.append(writable_result())
    if profile == "api":
        checks.extend((module_result("fastapi"), module_result("uvicorn")))
    failed = sum(item["status"] == "FAIL" for item in checks)
    return {
        "schema_version": "1.0",
        "project": PROJECT,
        "profile": profile,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "status": "READY" if failed == 0 else "BLOCKED",
        "summary": {"total": len(checks), "passed": len(checks) - failed, "failed": failed},
        "checks": checks,
    }


def write_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Project environment report",
        "",
        f"- Project: `{report['project']}`",
        f"- Profile: `{report['profile']}`",
        f"- Status: **{report['status']}**",
        f"- Checks: {report['summary']['passed']}/{report['summary']['total']} available",
        "",
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    for check in report["checks"]:
        detail = check["detail"].replace("|", "\\|")
        lines.append(f"| {check['label']} | {check['status']} | {detail} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("core", "api"), default="core")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.profile)
    if args.json_output:
        write_json(args.json_output, report)
    if args.markdown_output:
        write_markdown(args.markdown_output, report)
    for check in report["checks"]:
        print(f"[{check['status']}] {check['label']}: {check['detail']}")
    print(f"Environment status: {report['status']}")
    return 0 if report["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
