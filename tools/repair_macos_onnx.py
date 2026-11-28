#!/usr/bin/env python3
"""Repair only packaged ORT Mach-O references, then sign the modified binaries.

Nuitka collects native dependencies first. This final pass resolves ORT's
wheel-relative references against those collected binaries, never the build
venv. No libraries are removed, and missing dependencies fail the build.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import subprocess
import sys
from pathlib import Path


def capture(arguments: list[str]) -> str:
    return subprocess.run(arguments, check=True, capture_output=True, text=True).stdout


def repair(bundle: Path, *, runner=capture) -> list[Path]:
    contents = bundle.resolve() / "Contents"
    binaries = [path for path in contents.rglob("*") if path.is_file()]
    ort = [
        path
        for path in binaries
        if not path.is_symlink()
        and "onnxruntime" in path.name.lower()
        and path.suffix in {".so", ".dylib"}
    ]
    if not ort:
        raise RuntimeError("App bundle is missing ONNX Runtime native binaries")
    by_name: dict[str, list[Path]] = {}
    for path in binaries:
        if not path.resolve().is_relative_to(contents):
            raise RuntimeError(f"Packaged dependency escapes app bundle: {path}")
        by_name.setdefault(path.name, []).append(path)
    for binary in ort:
        dependencies = runner(["otool", "-L", str(binary)]).splitlines()[1:]
        for line in dependencies:
            dependency = re.split(r"\s+\(compatibility version", line.strip())[0]
            if not dependency or dependency.startswith(("/System/Library/", "/usr/lib/")):
                continue
            if dependency.startswith("@loader_path/"):
                resolved = (binary.parent / dependency.removeprefix("@loader_path/")).resolve()
                if resolved.is_relative_to(contents) and resolved.is_file():
                    continue
            if dependency.startswith("@executable_path/"):
                resolved = (
                    contents / "MacOS" / dependency.removeprefix("@executable_path/")
                ).resolve()
                if resolved.is_relative_to(contents) and resolved.is_file():
                    continue
            matches = by_name.get(Path(dependency).name, [])
            if len(matches) != 1:
                raise RuntimeError(
                    f"Unresolved or ambiguous ORT dependency: {binary}: {dependency}"
                )
            if matches[0].resolve() == binary.resolve() and binary.suffix == ".dylib":
                runner(["install_name_tool", "-id", f"@rpath/{binary.name}", str(binary)])
                continue
            # Mach-O install names use POSIX separators regardless of the host.
            replacement = "@loader_path/" + posixpath.relpath(
                matches[0].as_posix(), binary.parent.as_posix()
            )
            runner(["install_name_tool", "-change", dependency, replacement, str(binary)])
        runner(["codesign", "--force", "--sign", "-", str(binary)])
    return ort


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-bundle", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Mach-O repair requires macOS")
    for path in repair(args.app_bundle):
        print(f"Verified and signed packaged ORT binary: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
