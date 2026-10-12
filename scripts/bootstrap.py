from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib
import venv


ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = (ROOT / "build").resolve()
MARKER_NAME = "runtime.json"
MARKER_SCHEMA = 1
INSTALL_EXTRAS = ""


def _managed_cache_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return (base / "ZliY221" / "project-runtimes").resolve()


MANAGED_CACHE_ROOT = _managed_cache_root()


def _project_metadata() -> tuple[str, str, str]:
    pyproject = ROOT / "pyproject.toml"
    content = pyproject.read_bytes()
    project = tomllib.loads(content.decode("utf-8"))["project"]
    return project["name"], project["version"], hashlib.sha256(content).hexdigest()


def _runtime_python(runtime_dir: Path) -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return runtime_dir / relative


def _checkout_key() -> str:
    normalized = os.path.normcase(str(ROOT.resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _default_runtime_dir() -> Path:
    name, _, _ = _project_metadata()
    return MANAGED_CACHE_ROOT / f"{name}-{_checkout_key()}"


def _resolve_runtime_dir(value: Path | None) -> Path:
    if value is None:
        return _default_runtime_dir()
    candidate = value if value.is_absolute() else ROOT / value
    resolved = candidate.resolve()
    allowed_roots = (BUILD_ROOT, MANAGED_CACHE_ROOT)
    if not any(resolved != root and resolved.is_relative_to(root) for root in allowed_roots):
        raise ValueError("runtime directory must be below the project build or managed cache directory")
    return resolved


def _runtime_signature() -> dict[str, object]:
    name, version, pyproject_hash = _project_metadata()
    return {
        "schema": MARKER_SCHEMA,
        "project": name,
        "project_version": version,
        "checkout_key": _checkout_key(),
        "pyproject_sha256": pyproject_hash,
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "install_extras": INSTALL_EXTRAS,
    }


def _read_marker(runtime_dir: Path) -> dict[str, object] | None:
    marker = runtime_dir / MARKER_NAME
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write_marker(runtime_dir: Path, signature: dict[str, object]) -> None:
    marker = runtime_dir / MARKER_NAME
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(signature, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(marker)


def _prepare_runtime(runtime_dir: Path, refresh: bool) -> Path:
    signature = _runtime_signature()
    python = _runtime_python(runtime_dir)
    if not refresh and python.is_file() and _read_marker(runtime_dir) == signature:
        print(f"Reusing isolated runtime: {runtime_dir}", flush=True)
        return python

    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    print(f"Creating isolated runtime: {runtime_dir}", flush=True)
    venv.EnvBuilder(with_pip=True).create(runtime_dir)
    python = _runtime_python(runtime_dir)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--editable",
            str(ROOT),
        ],
        cwd=ROOT,
        check=True,
    )
    _write_marker(runtime_dir, signature)
    return python


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare an isolated project runtime and execute the public quickstart"
    )
    parser.add_argument(
        "--runtime-dir", type=Path
    )
    parser.add_argument("--refresh-runtime", action="store_true")
    args, quickstart_args = parser.parse_known_args()
    if quickstart_args[:1] == ["--"]:
        quickstart_args = quickstart_args[1:]

    try:
        runtime_dir = _resolve_runtime_dir(args.runtime_dir)
        python = _prepare_runtime(runtime_dir, args.refresh_runtime)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Runtime preparation failed: {exc}", file=sys.stderr)
        return exc.returncode if isinstance(exc, subprocess.CalledProcessError) else 2

    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    command = [str(python), str(ROOT / "scripts" / "quickstart.py"), *quickstart_args]
    return subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
