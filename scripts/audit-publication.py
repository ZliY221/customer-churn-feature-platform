#!/usr/bin/env python3
"""Audit tracked repository content before publication."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys


HIGH_RISK_PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    "github_token": re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    "aws_access_key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "openai_style_key": re.compile(rb"sk-[A-Za-z0-9]{20,}"),
}
NON_PROJECT_PATTERNS = {
    name: re.compile(term.encode("utf-8"), re.IGNORECASE)
    for name, term in {
        "resume_copy": "简历",
        "interview_copy": "面试",
        "job_search_copy": "求职",
        "internship_copy": "实习岗位",
        "learning_plan_copy": "学习计划",
        "exercise_copy": "动手练习",
        "lesson_objective_copy": "本单元目标",
        "reviewer_copy": "评审者",
    }.items()
}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    paths = [
        root / item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    ]
    matches: list[dict[str, object]] = []
    script_path = Path(__file__).resolve()
    for path in paths:
        if not path.is_file() or path.resolve() == script_path:
            continue
        content = path.read_bytes()
        if b"\0" in content:
            continue
        for group, patterns in (
            ("high_risk", HIGH_RISK_PATTERNS),
            ("non_project", NON_PROJECT_PATTERNS),
        ):
            for rule, pattern in patterns.items():
                lines = [
                    number
                    for number, line in enumerate(content.splitlines(), start=1)
                    if pattern.search(line)
                ]
                if lines:
                    matches.append(
                        {
                            "group": group,
                            "rule": rule,
                            "path": path.relative_to(root).as_posix(),
                            "lines": lines,
                        }
                    )
    report = {
        "status": "FAIL" if matches else "PASS",
        "repository": root.name,
        "tracked_file_count": len(paths),
        "matches": matches,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if matches:
        sys.exit(1)


if __name__ == "__main__":
    main()
