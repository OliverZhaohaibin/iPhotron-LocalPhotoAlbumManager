#!/usr/bin/env python3
"""Measure source DINOv2 initialization/inference in fresh processes, never Nuitka.

Legacy comparison is an explicit model-development operation. Application cold
start, GPU utilization and packaged acceptance must be measured separately.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def peak_rss() -> int:
    if sys.platform != "win32":
        import resource

        return int(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024)
        )
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            *[
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ],
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(counters.PeakWorkingSetSize)


def worker(args) -> dict:
    started = time.perf_counter()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    import numpy as np
    from PIL import Image

    from iPhoto.pets.image_utils import image_to_chw_float
    from iPhoto.pets.repository_utils import normalize_vector

    imported = time.perf_counter()
    tensors = []
    for path in args.image:
        with Image.open(path) as image:
            tensors.append(image_to_chw_float(image, (224, 224)))
    if not tensors:
        raise ValueError("Provide at least one public or disposable --image")
    loading = time.perf_counter()
    if args.worker == "torchscript":
        import torch
        from export_dinov2_onnx import SOURCE_SHA256, sha256

        if sha256(args.legacy_torchscript) != SOURCE_SHA256:
            raise ValueError("Legacy input is not the verified release model")
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        model = torch.jit.load(str(args.legacy_torchscript), map_location="cpu").eval()

        def infer(tensor):
            with torch.no_grad():
                return normalize_vector(model(torch.from_numpy(tensor)).numpy().reshape(-1))

        close = lambda: None
        providers = ["TorchScript:CPU"]
    else:
        from iPhoto.pets.pipeline import _DinoV2Embedder

        model = _DinoV2Embedder(
            args.model_dir,
            model_name="dinov2_vits14",
            allow_model_download=False,
            execution_providers=args.provider,
        )

        def infer(tensor):
            return normalize_vector(
                model._session.run(["embedding"], {"image": tensor})[0].reshape(-1)
            )

        close = model._runtime.close
        providers = model._session.get_providers()
    initialized = time.perf_counter()
    before = time.perf_counter()
    infer(tensors[0])
    first_ms = (time.perf_counter() - before) * 1000
    times = []
    cpu_start, wall_start = time.process_time(), time.perf_counter()
    for index in range(args.iterations):
        before = time.perf_counter()
        output = infer(tensors[index % len(tensors)])
        times.append((time.perf_counter() - before) * 1000)
        if not np.isfinite(output).all():
            raise RuntimeError("Non-finite benchmark output")
    elapsed, cpu = time.perf_counter() - wall_start, time.process_time() - cpu_start
    result = {
        "runtime": args.worker,
        "providers": providers,
        "threads_per_session": 2,
        "source_import_ms": (imported - started) * 1000,
        "model_initialization_ms": (initialized - loading) * 1000,
        "first_inference_ms": first_ms,
        "iterations": args.iterations,
        "inference_p50_ms": float(np.percentile(times, 50)),
        "inference_p95_ms": float(np.percentile(times, 95)),
        "images_per_second": args.iterations / elapsed,
        "process_cpu_seconds": cpu,
        "wall_seconds": elapsed,
        "mean_cpu_cores_used": cpu / elapsed,
        "peak_rss_bytes": peak_rss(),
        "gpu_utilization": "not measured",
    }
    close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--legacy-torchscript", type=Path)
    parser.add_argument("--image", type=Path, action="append", default=[])
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--provider", action="append", default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", choices=("onnx", "torchscript"))
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    args.provider = args.provider or ["CPUExecutionProvider"]
    if args.worker:
        print(json.dumps(worker(args)))
        return 0
    results = []
    for kind in ["onnx", "torchscript"] if args.legacy_torchscript else ["onnx"]:
        command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--worker", kind]
        start = time.perf_counter()
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        result = json.loads(completed.stdout)
        result["fresh_process_wall_ms"] = (time.perf_counter() - start) * 1000
        results.append(result)
    report = {
        "scope": "source DINOv2 model microbenchmark; not packaged app startup or full-library throughput",
        "platform": sys.platform,
        "machine": os.uname().machine
        if hasattr(os, "uname")
        else os.environ.get("PROCESSOR_ARCHITECTURE"),
        "results": results,
        "packaged_validation": "Pending Manual Validation",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
