#!/usr/bin/env python3
"""Generate a canonical packaged-build manifest for startup A/B evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_path(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.is_file():
        return _sha256_file(path)
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*"), key=lambda candidate: candidate.as_posix()):
        if not item.is_file():
            continue
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(item).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _installed_distributions() -> list[str]:
    entries = {
        f"{distribution.metadata['Name'].casefold()}=={distribution.version}"
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    return sorted(entries)


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def _git_revision(root: Path) -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        result = subprocess.run(  # noqa: S603 - resolved trusted git executable
            [git, "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_manifest(
    *,
    root: Path,
    artifact: Path,
    build_driver: Path,
    build_flags: list[str],
    native_runtime: Path | None,
    assets: list[Path],
) -> dict[str, Any]:
    dependencies = _installed_distributions()

    def _asset_label(path: Path) -> str:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.name

    environment = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "pyside6_version": _package_version("PySide6"),
        "qt_version": _package_version("PySide6_Essentials"),
        "nuitka_version": _package_version("Nuitka"),
        "build_driver_sha256": _sha256_path(build_driver),
        "build_flags": sorted(str(flag) for flag in build_flags),
        "dependency_snapshot_sha256": _canonical_hash(dependencies),
        "native_runtime_sha256": (
            _sha256_path(native_runtime) if native_runtime is not None else "not-included"
        ),
        "assets_sha256": _canonical_hash(
            {
                _asset_label(path): _sha256_path(path)
                for path in sorted(assets, key=_asset_label)
            }
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "source_revision": _git_revision(root),
        "artifact_path": artifact.name,
        "artifact_sha256": _sha256_path(artifact),
        "environment": environment,
        "environment_fingerprint": _canonical_hash(environment),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--build-driver", type=Path, required=True)
    parser.add_argument("--build-flag", action="append", default=[])
    parser.add_argument("--native-runtime", type=Path)
    parser.add_argument("--asset", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.expanduser().resolve()
    artifact = args.artifact.expanduser().resolve()
    build_driver = args.build_driver.expanduser().resolve()
    if not artifact.exists():
        raise SystemExit(f"build manifest error: artifact does not exist: {artifact}")
    if not build_driver.is_file():
        raise SystemExit(f"build manifest error: build driver does not exist: {build_driver}")
    manifest = create_manifest(
        root=root,
        artifact=artifact,
        build_driver=build_driver,
        build_flags=args.build_flag,
        native_runtime=(
            args.native_runtime.expanduser().resolve() if args.native_runtime else None
        ),
        assets=[path.expanduser().resolve() for path in args.asset],
    )
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
