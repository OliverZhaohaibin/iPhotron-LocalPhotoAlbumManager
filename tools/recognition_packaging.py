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
import os
import platform
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORT_DISTRIBUTIONS = {
    "cpu": "onnxruntime",
    "cuda": "onnxruntime-gpu",
    "directml": "onnxruntime-directml",
    "openvino": "onnxruntime-openvino",
}
FORBIDDEN = {"torch", "torchvision", "torchaudio", "triton", "pytorch-triton"}
MODEL_CONTRACT = ROOT / "src/iPhoto/recognition/recognition_package.json"
# Match native family names, including versioned Linux SONAMEs and Windows
# suffixes. Looking only for cudart/cublas/cudnn misses most CUDA payloads.
CUDA_NATIVE = re.compile(
    r"^(?:lib)?(?:cuda|cudart|cudadevrt|cudnn|cublas|cufft|curand|cusolver|cusparse|"
    r"cupti|cufile|cudss|cutensor|cuquantum|custatevec|cutensornet|nccl|nvshmem|"
    r"nvrtc|nvjitlink|nvptxcompiler|nvfatbin|nvblas|nvjpeg|npp|nvtoolsext|nvtx|"
    r"nvinfer|nvonnxparser|nvparsers|nvidia|nvcuvid|nvencodeapi|nvml|nvvm|nvcomp|"
    r"nvperf|nvoptix|pcsamplingutil|accinj|cuinj|cudbg)"
    r"[a-z0-9_.-]*\.(?:so(?:\.[0-9.]+)?|dll|dylib|a|lib)$",
    re.IGNORECASE,
)
LARGE_FILE_BYTES = 1024 * 1024


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
    contract = json.loads(MODEL_CONTRACT.read_text())
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


def forbidden_native(name: str, profile: str) -> bool:
    normalized = name.replace("\\", "/").lower()
    basename = normalized.rsplit("/", 1)[-1]
    return (
        forbidden(basename.split(".")[0], profile)
        or basename.startswith(
            ("libtorch", "torch_", "libc10", "c10", "libtorchvision", "libtorchaudio", "libtriton")
        )
        or (profile != "cuda" and bool(CUDA_NATIVE.fullmatch(basename)))
        or (profile != "cuda" and Path(basename).suffix in {".cubin", ".fatbin", ".ptx"})
        or (
            profile == "cpu"
            and any(
                token in basename
                for token in (
                    "providers_cuda",
                    "providers_tensorrt",
                    "directml.dll",
                    "providers_openvino",
                )
            )
        )
    )


def _run_inspector(args: list[str]) -> str:
    try:
        return subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"Native dependency inspection failed: {args}: {exc}") from exc


def _ordered_dumpbins(paths) -> list[Path]:
    host = (os.environ.get("PROCESSOR_ARCHITEW6432") or platform.machine()).casefold()
    host = {"amd64": "x64", "aarch64": "arm64", "i386": "x86", "i686": "x86"}.get(host, host)

    def priority(path):
        version = tuple(-int(part) for part in re.findall(r"\d+", path.parents[3].name))
        return (
            path.parent.parent.name.casefold() != f"host{host}",
            path.parent.name.casefold() != host,
            version,
            str(path).casefold(),
        )

    return sorted({path.resolve() for path in paths if path.is_file()}, key=priority)


@lru_cache(maxsize=1)
def _vswhere_dumpbins() -> tuple[Path, ...]:
    # Ordinary PowerShell does not inherit the Visual Studio developer PATH.
    # Cache discovery per audit process, rather than launching vswhere per DLL.
    candidates = []
    if located := shutil.which("vswhere"):
        candidates.append(Path(located))
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        if base := os.environ.get(variable):
            candidates.append(Path(base) / "Microsoft Visual Studio/Installer/vswhere.exe")
    for candidate in dict.fromkeys(candidates):
        if not candidate.is_file():
            continue
        try:
            output = _run_inspector([
                str(candidate.resolve()), "-products", "*", "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-find",
                r"VC\Tools\MSVC\**\bin\Host*\*\dumpbin.exe", "-utf8",
            ])
        except ValueError:
            continue
        tools = _ordered_dumpbins(Path(line.strip()) for line in output.splitlines() if line.strip())
        if tools:
            return tuple(tools)
    return ()


def _pe_inspector_candidates():
    for name in ("llvm-readobj", "dumpbin", "objdump"):
        if located := shutil.which(name):
            yield Path(located).resolve()
    for variable, pattern in (
        ("VCToolsInstallDir", "bin/Host*/*/dumpbin.exe"),
        ("VSINSTALLDIR", "VC/Tools/MSVC/*/bin/Host*/*/dumpbin.exe"),
    ):
        if root := os.environ.get(variable):
            yield from _ordered_dumpbins(Path(root).glob(pattern))
    yield from _vswhere_dumpbins()


def _inspect_pe(path: Path, runner) -> tuple[str, list[str]]:
    failures, seen = [], set()
    for tool in _pe_inspector_candidates():
        if tool in seen:
            continue
        seen.add(tool)
        name = tool.name.lower()
        if "llvm-readobj" in name:
            arguments = [str(tool), "--coff-imports", str(path)]
            pattern = r"^\s*Name:\s*(.+?\.dll)\s*$"
        elif "dumpbin" in name:
            arguments = [str(tool), "/DEPENDENTS", str(path)]
            pattern = r"^\s*(\S+\.dll)\s*$"
        else:
            arguments = [str(tool), "-p", str(path)]
            pattern = r"DLL Name:\s*(\S+)"
        try:
            output = runner(arguments)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            failures.append(f"{tool}: {exc}")
            continue
        return str(tool), re.findall(pattern, output, re.MULTILINE | re.IGNORECASE)
    raise ValueError(
        "Install a working llvm-readobj, dumpbin or objdump to audit PE imports. "
        "MSVC tools are also discovered through VCToolsInstallDir, VSINSTALLDIR and vswhere. "
        + "; ".join(failures)
    )


def native_dependencies(path: Path, *, runner=None) -> dict | None:
    """Read import tables without loading/executing the inspected binary."""
    runner = runner or _run_inspector
    with path.open("rb") as stream:
        magic = stream.read(4)
    if magic == b"\x7fELF":
        tool = shutil.which("readelf") or shutil.which("llvm-readelf")
        if not tool:
            raise ValueError("Install readelf or llvm-readelf to audit ELF dependencies")
        output = runner([tool, "--dynamic", "--wide", str(path)])
        dependencies = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", output)
        kind = "ELF"
    elif magic in {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }:
        tool = shutil.which("otool")
        if not tool:
            raise ValueError("Install otool to audit Mach-O dependencies")
        output = runner([tool, "-L", str(path)])
        dependencies = re.findall(r"^\s+(.+?)\s+\(compatibility version", output, re.MULTILINE)
        # otool also prints a dylib's LC_ID_DYLIB, which is not a dependency.
        dependencies = [name for name in dependencies if Path(name).name != path.name]
        kind = "Mach-O"
    elif magic[:2] == b"MZ":
        tool, dependencies = _inspect_pe(path, runner)
        kind = "PE"
    else:
        return None
    return {
        "format": kind,
        "inspector": Path(tool).name,
        "inspector_path": str(Path(tool).resolve()),
        "dependencies": sorted(set(dependencies)),
    }


def check_native_inspector() -> dict:
    report = native_dependencies(Path(sys.executable))
    if report is None or (report["format"] == "PE" and not report["dependencies"]):
        raise ValueError(f"Cannot read native imports from build Python: {sys.executable}")
    return report


def artifact_report(tree: Path, profile: str, *, require_runtime: bool = True) -> dict:
    entries, bad, components = [], [], defaultdict(int)
    native_imports, models = {}, {}
    large_by_size = defaultdict(list)
    model_contract = json.loads(MODEL_CONTRACT.read_text())["files"]
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
        if forbidden_native(relative, profile):
            bad.append(relative)
        if "extension/models/" not in relative and path.suffix.lower() in {
            ".onnx",
            ".pt",
            ".pth",
            ".ckpt",
            ".safetensors",
        }:
            bad.append(f"unexpected model resource: {relative}")
        if not path.is_symlink():
            imports = native_dependencies(path)
            if imports is not None:
                native_imports[relative] = imports
                bad.extend(
                    f"{relative} -> {name}"
                    for name in imports["dependencies"]
                    if forbidden_native(name, profile)
                )
        if "extension/models/" in relative:
            name = relative.split("extension/models/", 1)[1]
            expected = model_contract.get(name)
            if (
                expected is None
                or path.stat().st_size != expected["size"]
                or digest(path) != expected["sha256"]
            ):
                bad.append(f"unverified model resource: {relative}")
            models[name] = relative
        size = 0 if path.is_symlink() else path.stat().st_size
        if size >= LARGE_FILE_BYTES:
            large_by_size[size].append(path)
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
    if models and models.keys() != model_contract.keys():
        raise ValueError(
            f"Incomplete bundled model set: {sorted(model_contract.keys() - models.keys())}"
        )
    if require_runtime and not ort_native:
        raise ValueError("ONNX Runtime native binding is missing from the standalone tree")
    duplicates = []
    for size, paths in large_by_size.items():
        if len(paths) < 2:
            continue
        by_hash = defaultdict(list)
        for path in paths:
            by_hash[digest(path)].append(path.relative_to(tree).as_posix())
        duplicates.extend(
            {
                "sha256": sha256,
                "paths": names,
                "size_each": size,
                "duplicate_bytes": size * (len(names) - 1),
            }
            for sha256, names in by_hash.items()
            if len(names) > 1
        )
    return {
        "profile": profile,
        "standalone_bytes": sum(entry["size"] for entry in entries),
        "file_count": len(entries),
        "component_bytes": dict(components),
        "largest_files": sorted(entries, key=lambda entry: (-entry["size"], entry["path"]))[:50],
        "torch_free": True,
        "ort_native_present": ort_native,
        "native_imports": native_imports,
        "duplicate_large_files": sorted(
            duplicates, key=lambda item: (-item["duplicate_bytes"], item["paths"])
        ),
        "model_files": [entry for entry in entries if "extension/models/" in entry["path"]],
    }


def check_compilation_report(path: Path, profile: str = "cpu") -> None:
    report = ET.parse(path)
    bad = [
        node.attrib.get("name", "")
        for node in report.iter("module")
        if forbidden(node.attrib.get("name", "").split(".")[0], profile)
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
    parser.add_argument("--check-native-inspector", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "environment":
            report = environment_report(args.profile, require_complete=args.require_complete)
            if args.check_native_inspector:
                report["native_inspector"] = check_native_inspector()
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
                check_compilation_report(args.nuitka_report, args.profile)
                report["nuitka_report_sha256"] = digest(args.nuitka_report)
            if args.artifact:
                report["artifact_bytes"] = args.artifact.stat().st_size
                report["artifact_sha256"] = digest(args.artifact)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except (ValueError, OSError, ET.ParseError) as exc:
        parser.exit(2, f"Recognition packaging contract: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
