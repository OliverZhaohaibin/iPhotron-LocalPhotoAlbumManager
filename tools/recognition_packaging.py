#!/usr/bin/env python3
"""Fail closed on polluted build environments, unverified models, or Torch payloads.

This performs inspection/staging only. It never compiles an application, deletes
native libraries, or executes a model. Use before and after each platform build.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import shutil
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORT_DISTRIBUTIONS = {
    "cpu": "onnxruntime",
    "cuda": "onnxruntime-gpu",
    "directml": "onnxruntime-directml",
    "openvino": "onnxruntime-openvino",
}
FORBIDDEN = {"torch", "torchvision", "torchaudio", "triton", "pytorch-triton"}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def forbidden(name: str, profile: str) -> bool:
    name = canonical(name)
    return (
        name in FORBIDDEN
        or name.startswith("pytorch-triton")
        or ((name == "nvidia" or name.startswith("nvidia-")) and profile != "cuda")
    )


def environment_report(profile: str, *, require_complete: bool = False) -> dict:
    distributions = list(importlib.metadata.distributions())
    packages = {
        canonical(item.metadata["Name"]): {
            "version": item.version,
            "requires": sorted(item.requires or []),
        }
        for item in distributions
        if item.metadata.get("Name")
    }
    bad = [name for name in packages if forbidden(name, profile)]
    ort = sorted(
        {
            canonical(item.metadata["Name"])
            for item in distributions
            if item.metadata.get("Name")
            and (
                canonical(item.metadata["Name"]) in ORT_DISTRIBUTIONS.values()
                or any(
                    str(path).replace("\\", "/").startswith("onnxruntime/")
                    for path in (getattr(item, "files", None) or ())
                )
            )
        }
    )
    if bad or ort != [ORT_DISTRIBUTIONS[profile]]:
        raise ValueError(
            f"Use a clean, isolated build venv. Forbidden distributions: {bad}; "
            f"ORT namespace must have exactly {ORT_DISTRIBUTIONS[profile]!r}, found {ort}. "
            "See docs/requirements/UNIFIED_ONNX_RUNTIME_VALIDATION.md."
        )
    if require_complete:
        missing = {"insightface", "usearch", "numpy", "pillow"} - packages.keys()
        if missing:
            raise ValueError(f"Incomplete recognition build runtime: {sorted(missing)}")
    return {"profile": profile, "distributions": packages, "torch_free": True}


def stage_models(source: Path, output: Path) -> dict:
    contract = json.loads((ROOT / "src/iPhoto/recognition/recognition_package.json").read_text())
    files = contract["files"]
    # Validate all input bytes before touching the destination. Only explicitly
    # whitelisted ONNX artifacts and metadata enter frozen bundles.
    for name, expected in files.items():
        path = source / name
        if path.stat().st_size != expected["size"] or digest(path) != expected["sha256"]:
            raise ValueError(f"Invalid recognition model: {path}")
    if output.exists():
        raise ValueError(f"Refusing to overwrite staged models: {output}")
    output.mkdir(parents=True)
    try:
        for name in files:
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
    except Exception:
        shutil.rmtree(output)
        raise
    return {"files": files, "model_bytes": sum(item["size"] for item in files.values())}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def artifact_report(tree: Path, profile: str, *, require_runtime: bool = True) -> dict:
    entries, bad, components = [], [], defaultdict(int)
    ort_native = False
    for path in sorted(tree.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(tree).as_posix()
        lower = relative.lower()
        if path.is_symlink() and not path.resolve().is_relative_to(tree.resolve()):
            bad.append(relative)
        if any(
            forbidden(part.split(".")[0], profile)
            or part.startswith(("libtorch", "torch_cpu", "torch_cuda", "libc10", "c10_cuda"))
            for part in lower.split("/")
        ) or ("extension/models/" in lower and path.suffix.lower() in {".pt", ".pth"}):
            bad.append(relative)
        if profile == "cpu" and any(
            token in lower
            for token in (
                "providers_cuda",
                "providers_tensorrt",
                "cudnn",
                "cublas",
                "cudart",
                "directml.dll",
                "providers_openvino",
            )
        ):
            bad.append(relative)
        size = 0 if path.is_symlink() else path.stat().st_size
        entries.append({"path": relative, "size": size})
        component = (
            "models"
            if "extension/models/" in lower
            else "onnxruntime"
            if "onnxruntime" in lower or "directml.dll" in lower
            else "maps"
            if "/maps/" in f"/{lower}"
            else "qt"
            if "pyside6" in lower or "/qt" in lower
            else "other"
        )
        components[component] += size
        ort_native |= "onnxruntime_pybind11_state" in lower and path.suffix in {
            ".so",
            ".pyd",
            ".dylib",
        }
    if bad:
        raise ValueError(f"Forbidden production payloads: {bad}")
    if require_runtime and not ort_native:
        raise ValueError("ONNX Runtime native binding is missing from the standalone tree")
    return {
        "profile": profile,
        "standalone_bytes": sum(entry["size"] for entry in entries),
        "file_count": len(entries),
        "component_bytes": dict(components),
        "largest_files": sorted(entries, key=lambda entry: (-entry["size"], entry["path"]))[:50],
        "torch_free": True,
        "ort_native_present": ort_native,
        "model_files": [entry for entry in entries if "extension/models/" in entry["path"]],
    }


def check_compilation_report(path: Path) -> None:
    report = ET.parse(path)
    bad = [
        node.attrib.get("name", "")
        for node in report.iter("module")
        if node.attrib.get("name", "").split(".")[0] in FORBIDDEN
    ]
    if bad:
        raise ValueError(f"Nuitka followed forbidden inference modules: {bad}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("environment", "stage-models", "artifact"))
    parser.add_argument("--profile", choices=ORT_DISTRIBUTIONS, default="cpu")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--tree", type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--nuitka-report", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "environment":
            report = environment_report(args.profile, require_complete=args.require_complete)
        elif args.action == "stage-models":
            if args.source is None:
                parser.error("stage-models requires --source")
            stage_models(args.source, args.output)
            return 0
        else:
            if args.tree is None or not args.tree.is_dir():
                parser.error("artifact requires an existing --tree")
            report = artifact_report(args.tree, args.profile)
            if args.nuitka_report:
                check_compilation_report(args.nuitka_report)
                report["nuitka_report_sha256"] = digest(args.nuitka_report)
            if args.artifact:
                report["artifact_bytes"] = args.artifact.stat().st_size
                report["artifact_sha256"] = digest(args.artifact)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Recognition packaging contract: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
