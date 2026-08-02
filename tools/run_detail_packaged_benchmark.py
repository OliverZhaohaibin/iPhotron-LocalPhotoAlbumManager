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

from PIL import Image, ImageDraw
from pillow_heif import register_heif_opener

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
            path for path in macos_dir.iterdir() if path.is_file() and os.access(path, os.X_OK)
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
        switch_paths = item.get("switch_paths", [])
        if not isinstance(switch_paths, list):
            raise ValueError("sample switch_paths must be an array")
        relatives.extend(Path(str(value)) for value in switch_paths)
        for relative in relatives:
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"sample path must remain within the library: {relative}")
    return payload


def _copy_benchmark_library(
    source_root: Path,
    destination: Path,
    samples: list[dict],
) -> list[dict]:
    """Copy selected assets into one disposable album and rewrite the plan.

    The production Gallery store exposes rows from the currently open album,
    not every nested album in a library.  Flattening only the selected samples
    keeps the packaged driver on that production store while unique numeric
    prefixes avoid basename collisions.  HEIC/MOV companions keep the same
    rewritten stem so Live Photo discovery remains representative.
    """

    destination.mkdir(parents=True, exist_ok=True)
    copied: set[Path] = set()
    target_stems: dict[Path, str] = {}

    def target_for(relative: Path) -> Path:
        source_stem = relative.with_suffix("")
        target_stem = target_stems.get(source_stem)
        if target_stem is None:
            safe_stem = "".join(
                character if character.isalnum() or character in {"-", "_"} else "_"
                for character in relative.stem
            )
            target_stem = f"{len(target_stems):03d}-{safe_stem}"
            target_stems[source_stem] = target_stem
        return Path(f"{target_stem}{relative.suffix}")

    def copy_one(relative: Path) -> None:
        if relative in copied:
            return
        copied.add(relative)
        source = source_root / relative
        if not source.is_file():
            raise ValueError(f"sample does not exist: {source}")
        target = destination / target_for(relative)
        shutil.copy2(source, target)
        sidecar = source.with_suffix(".ipo")
        if sidecar.is_file():
            shutil.copy2(sidecar, target.with_suffix(".ipo"))

        # Copy an available Live Photo companion without adding a second
        # benchmark transaction for it.
        for suffix in (".HEIC", ".heic", ".MOV", ".mov"):
            companion_relative = relative.with_suffix(suffix)
            companion = source_root / companion_relative
            if companion_relative not in copied and companion.is_file():
                copied.add(companion_relative)
                shutil.copy2(companion, destination / target_for(companion_relative))

    rewritten: list[dict] = []
    for item in samples:
        relatives = [Path(str(item["path"]))]
        relatives.extend(Path(str(value)) for value in item.get("switch_paths", ()))
        for relative in relatives:
            copy_one(relative)
        rewritten.append(
            {
                **item,
                "path": str(target_for(relatives[0])),
                **(
                    {"switch_paths": [str(target_for(value)) for value in relatives[1:]]}
                    if relatives[1:]
                    else {}
                ),
            }
        )
    return rewritten


def _generate_deterministic_48mp_samples(destination: Path) -> list[dict[str, str]]:
    """Create repeatable large inputs in the disposable benchmark library."""

    generated_dir = destination
    image = Image.new("RGB", (8000, 6000), (16, 24, 32))
    draw = ImageDraw.Draw(image)
    for y in range(0, image.height, 256):
        for x in range(0, image.width, 256):
            cell = (x // 256) + (y // 256) * 31
            draw.rectangle(
                (x, y, min(x + 255, image.width - 1), min(y + 255, image.height - 1)),
                fill=((cell * 37) % 256, (cell * 67) % 256, (cell * 97) % 256),
            )
    jpeg = generated_dir / "deterministic-48mp.jpg"
    heic = generated_dir / "deterministic-48mp.heic"
    image.save(jpeg, format="JPEG", quality=92, subsampling=0)
    register_heif_opener()
    image.save(heic, format="HEIF", quality=90)
    image.close()
    return [
        {"path": str(jpeg.relative_to(destination)), "category": "jpeg-48mp"},
        {"path": str(heic.relative_to(destination)), "category": "heic-48mp"},
    ]


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
    for stale_result in (
        events_path,
        metadata_path,
        summary_path,
        validation_path,
        output_dir / "comparison.json",
        output_dir / "comparison_validation.json",
    ):
        stale_result.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="iphoto-detail-benchmark-") as temporary:
        library_copy = Path(temporary) / "library"
        samples = _copy_benchmark_library(source_library, library_copy, manifest["samples"])
        if not args.skip_generated_48mp:
            samples.extend(_generate_deterministic_48mp_samples(library_copy))
        plan = {
            **manifest,
            "samples": samples,
            "library_root": str(library_copy),
            "repetitions": max(1, int(args.repetitions)),
        }
        plan_path = Path(temporary) / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        environment = os.environ.copy()
        benchmark_home = Path(temporary) / "home"
        benchmark_home.mkdir()
        environment.update(
            {
                # Do not probe or migrate the developer's remembered Library.
                "HOME": str(benchmark_home),
                "XDG_CACHE_HOME": str(benchmark_home / ".cache"),
                "XDG_CONFIG_HOME": str(benchmark_home / ".config"),
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
    parser.add_argument("--skip-generated-48mp", action="store_true")
    parser.add_argument("--commit", default="unknown")
    parser.add_argument("--build-label", choices=("baseline", "candidate"), default="candidate")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
