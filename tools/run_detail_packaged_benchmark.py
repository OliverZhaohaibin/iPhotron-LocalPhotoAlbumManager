#!/usr/bin/env python3
"""Run the opt-in Detail harness against a packaged application."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.detail_benchmark import (
    compare,
    summarize,
    validate_comparison,
    validate_summary,
)


def _resolve_executable(value: Path) -> Path:
    candidate = value.expanduser().absolute()
    if candidate.suffix == ".app" and candidate.is_dir():
        macos_dir = candidate / "Contents" / "MacOS"
        executables = sorted(
            path for path in macos_dir.iterdir()
            if path.is_file() and os.access(path, os.X_OK)
        )
        if not executables:
            raise ValueError(f"app bundle has no executable: {candidate}")
        return executables[0]
    if not candidate.is_file():
        raise ValueError(f"packaged executable does not exist: {candidate}")
    return candidate


def _load_manifest(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    samples = payload.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("manifest requires a non-empty samples array")
    for item in samples:
        if not isinstance(item, dict) or not str(item.get("path") or "").strip():
            raise ValueError("each manifest sample requires a relative path")
        relatives = [Path(str(item["path"]))]
        switch_paths = item.get("switch_paths", ())
        if not isinstance(switch_paths, list):
            raise ValueError("sample switch_paths must be an array")
        relatives.extend(Path(str(value)) for value in switch_paths)
        for relative in relatives:
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"sample path must remain within the library: {relative}")
    return payload


def _copy_benchmark_library(source_root: Path, destination: Path, samples: list[dict]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    copied: set[Path] = set()
    for item in samples:
        relatives = [Path(str(item["path"]))]
        relatives.extend(Path(str(value)) for value in item.get("switch_paths", ()))
        for relative in relatives:
            if relative in copied:
                continue
            copied.add(relative)
            source = source_root / relative
            if not source.is_file():
                raise ValueError(f"sample does not exist: {source}")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            sidecar = source.with_suffix(".ipo")
            if sidecar.is_file():
                shutil.copy2(sidecar, target.with_suffix(".ipo"))


def run(args: argparse.Namespace) -> int:
    executable = _resolve_executable(args.app)
    source_library = args.library.expanduser().absolute()
    manifest = _load_manifest(args.manifest)
    output_dir = args.output_dir.expanduser().absolute()
    output_dir.mkdir(parents=True, exist_ok=True)
    events_path = output_dir / "events.jsonl"
    metadata_path = output_dir / "runtime.json"
    summary_path = output_dir / "summary.json"
    validation_path = output_dir / "validation.json"

    with tempfile.TemporaryDirectory(prefix="iphoto-detail-benchmark-") as temporary:
        library_copy = Path(temporary) / "library"
        _copy_benchmark_library(source_library, library_copy, manifest["samples"])
        plan = {
            **manifest,
            "library_root": str(library_copy),
            "repetitions": max(1, int(args.repetitions)),
        }
        plan_path = Path(temporary) / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "IPHOTO_DETAIL_PROFILE": "1",
                "IPHOTO_DETAIL_PROFILE_PATH": str(events_path),
                "IPHOTO_DETAIL_BENCHMARK_PLAN": str(plan_path),
                "IPHOTO_DETAIL_BENCHMARK_METADATA": str(metadata_path),
                "IPHOTO_DETAIL_BENCHMARK_COMMIT": str(args.commit),
                "IPHOTO_DETAIL_BENCHMARK_BUILD": str(args.build_label),
            }
        )
        completed = subprocess.run(  # noqa: S603 - explicit user-supplied packaged executable
            [str(executable)],
            env=environment,
            check=False,
            timeout=max(60, int(args.timeout_seconds)),
        )

    if not events_path.is_file() or not metadata_path.is_file():
        return completed.returncode or 2
    summary = summarize([events_path])
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    validation = validate_summary(summary, minimum_samples=max(1, int(args.repetitions)))
    validation_path.write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    comparison_validation = None
    if args.baseline_summary is not None:
        baseline = json.loads(args.baseline_summary.read_text(encoding="utf-8"))
        comparison = compare(baseline, summary)
        (output_dir / "comparison.json").write_text(
            json.dumps(comparison, indent=2) + "\n",
            encoding="utf-8",
        )
        comparison_validation = validate_comparison(comparison)
        (output_dir / "comparison_validation.json").write_text(
            json.dumps(comparison_validation, indent=2) + "\n",
            encoding="utf-8",
        )
    runtime = json.loads(metadata_path.read_text(encoding="utf-8"))
    passed = (
        completed.returncode == 0
        and runtime.get("result") == "complete"
        and validation["passed"]
        and (comparison_validation is None or comparison_validation["passed"])
    )
    return 0 if passed else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--baseline-summary", type=Path)
    parser.add_argument("--commit", default="unknown")
    parser.add_argument("--build-label", choices=("baseline", "candidate"), default="candidate")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
