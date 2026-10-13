from __future__ import annotations

import argparse
import json
from pathlib import Path

from .feature_registry import FeatureRegistry, compare_feature_sets, load_ndjson
from .inference import _atomic_write


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare registered offline and online feature snapshots"
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--offline", type=Path, required=True)
    parser.add_argument("--online", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    registry = FeatureRegistry.from_file(args.registry)
    report = compare_feature_sets(
        load_ndjson(args.offline),
        load_ndjson(args.online),
        registry,
    )
    _atomic_write(
        args.output,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
