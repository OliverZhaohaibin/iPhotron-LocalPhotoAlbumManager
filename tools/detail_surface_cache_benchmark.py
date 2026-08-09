#!/usr/bin/env python3
"""Benchmark neutral-surface cache I/O without requiring the full GUI runtime."""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import qVersion
from PySide6.QtGui import QImage

import iPhoto.gui.detail_surface_cache as cache_module
from iPhoto.core.color_resolver import ColorStats
from iPhoto.gui.detail_decode_backend import DecodedSurface
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailDecodeKey,
    DetailGeometryState,
    DetailRenderRequest,
)
from iPhoto.gui.detail_surface_cache import NeutralSurfaceStore
from iPhoto.infrastructure.services.thumbnail_runtime_policy import (
    resolve_physical_memory_bytes,
)

_MIB = 1024 * 1024


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return float(ordered[index])


def _request(root: Path, *, size_mib: int, revision: int) -> DetailRenderRequest:
    source = root / f"surface-{size_mib}.rgba"
    return DetailRenderRequest(
        generation=revision,
        asset_id=f"surface-{size_mib}",
        source_identity=AssetSourceIdentity.create(
            source,
            size_bytes=size_mib * _MIB,
            source_mtime_ns=revision,
            width=4096,
            height=max(1, size_mib * _MIB // (4096 * 4)),
            orientation=1,
        ),
        viewport_physical_size=(1920, 1080),
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState(),
        reason="cache-benchmark",
        decode_level="full",
    )


def _surface(request: DetailRenderRequest) -> DecodedSurface:
    width = int(request.source_identity.width)
    height = int(request.source_identity.height)
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    if image.isNull():
        raise MemoryError(f"unable to allocate benchmark QImage {width}x{height}")
    image.fill(0xFF123456)
    return DecodedSurface(
        image=image,
        decode_key=DetailDecodeKey.from_request(request),
        source_size=(width, height),
        decoded_size=(width, height),
        decode_level="full",
        backend="benchmark",
        color_stats=ColorStats(),
    )


def _store(root: Path) -> NeutralSurfaceStore:
    try:
        return NeutralSurfaceStore(root, budget_bytes=4 * 1024 * _MIB)
    except TypeError:
        # Fixed v2 baseline did not expose a budget override.
        return NeutralSurfaceStore(root)


def _rss_bytes() -> int:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return 0
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError):
            return 0
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value if sys.platform == "darwin" else value * 1024
    except (ImportError, OSError, ValueError):
        return 0


def _git_sha(repo_root: Path) -> str:
    executable = shutil.which("git")
    if executable is None:
        return "unknown"
    try:
        return subprocess.run(
            [executable, "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def benchmark(
    *,
    sizes_mib: tuple[int, ...],
    warm_samples: int,
    repo_root: Path,
    label: str,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="iphoto-surface-cache-") as temporary:
        base = Path(temporary)
        for revision, size_mib in enumerate(sizes_mib, start=1):
            library = base / f"library-{size_mib}"
            library.mkdir(parents=True)
            request = _request(library, size_mib=size_mib, revision=revision)
            surface = _surface(request)
            store = _store(library)

            checksum_calls = 0
            original_checksum = cache_module._checksum

            def counted_checksum(payload, checksum=original_checksum) -> int:
                nonlocal checksum_calls
                checksum_calls += 1
                return checksum(payload)

            cache_module._checksum = counted_checksum
            rss_before = _rss_bytes()
            tracemalloc.start()
            write_started = time.perf_counter()
            wrote = store.write(request, surface)
            write_ms = (time.perf_counter() - write_started) * 1000.0
            _current, python_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            rss_after_write = _rss_bytes()
            if not wrote:
                raise RuntimeError(f"surface cache write failed for {size_mib} MiB")

            close = getattr(store, "close", None)
            if callable(close):
                close()
            store = _store(library)
            first_started = time.perf_counter()
            loaded = store.load(request)
            first_hit_ms = (time.perf_counter() - first_started) * 1000.0
            if loaded is None:
                raise RuntimeError(f"surface cache first hit failed for {size_mib} MiB")
            del loaded
            gc.collect()

            warm_values: list[float] = []
            for _sample in range(max(1, warm_samples)):
                started = time.perf_counter()
                loaded = store.load(request)
                warm_values.append((time.perf_counter() - started) * 1000.0)
                if loaded is None:
                    raise RuntimeError(f"surface cache warm hit failed for {size_mib} MiB")
                del loaded
            gc.collect()
            cache_module._checksum = original_checksum
            close = getattr(store, "close", None)
            if callable(close):
                close()

            namespace = "unknown"
            root = getattr(store, "root", None)
            if isinstance(root, Path):
                namespace = root.name
            results.append(
                {
                    "size_mib": size_mib,
                    "payload_bytes": surface.image.sizeInBytes(),
                    "namespace": namespace,
                    "write_ms": round(write_ms, 3),
                    "first_hit_ms": round(first_hit_ms, 3),
                    "warm_hit_p50_ms": round(statistics.median(warm_values), 3),
                    "warm_hit_p95_ms": round(_percentile(warm_values, 0.95), 3),
                    "checksum_calls": checksum_calls,
                    "python_peak_bytes": int(python_peak),
                    "rss_write_delta_bytes": max(0, rss_after_write - rss_before),
                }
            )
            del surface
            gc.collect()

    return {
        "schema": 1,
        "label": label,
        "commit_sha": _git_sha(repo_root),
        "platform": platform.platform(),
        "runner_os": os.environ.get("RUNNER_OS", platform.system()),
        "runner_image": os.environ.get("ImageOS", "local"),
        "physical_memory_bytes": int(resolve_physical_memory_bytes()),
        "python": platform.python_version(),
        "pyside": pyside_version,
        "qt": qVersion(),
        "warm_samples": max(1, warm_samples),
        "results": results,
    }


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_by_size = {int(row["size_mib"]): row for row in baseline["results"]}
    checks: list[dict[str, Any]] = []
    for row in candidate["results"]:
        size = int(row["size_mib"])
        old = baseline_by_size[size]
        tolerance = max(float(old["warm_hit_p95_ms"]) * 0.05, 10.0)
        warm_limit = float(old["warm_hit_p95_ms"]) + tolerance
        payload = int(row["payload_bytes"])
        checks.extend(
            [
                {
                    "name": f"{size}MiB.trusted_hit_checksum_calls",
                    "actual": int(row["checksum_calls"]),
                    "limit": 0,
                    "passed": int(row["checksum_calls"]) == 0,
                },
                {
                    "name": f"{size}MiB.python_peak_bytes",
                    "actual": int(row["python_peak_bytes"]),
                    "limit": 8 * _MIB + 4096,
                    "passed": int(row["python_peak_bytes"]) <= 8 * _MIB + 4096,
                },
                {
                    "name": f"{size}MiB.rss_write_delta_bytes",
                    "actual": int(row["rss_write_delta_bytes"]),
                    "limit": max(16 * _MIB, int(payload * 0.25)),
                    "passed": int(row["rss_write_delta_bytes"])
                    <= max(16 * _MIB, int(payload * 0.25)),
                },
                {
                    "name": f"{size}MiB.warm_hit_p95_ms",
                    "actual": float(row["warm_hit_p95_ms"]),
                    "limit": round(warm_limit, 3),
                    "passed": float(row["warm_hit_p95_ms"]) <= warm_limit,
                },
            ]
        )
    return {
        "schema": 1,
        "baseline_sha": baseline.get("commit_sha"),
        "candidate_sha": candidate.get("commit_sha"),
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--output", required=True, type=Path)
    run_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    run_parser.add_argument("--label", default="candidate")
    run_parser.add_argument("--sizes-mib", default="16,64,180")
    run_parser.add_argument("--warm-samples", type=int, default=3)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--baseline", required=True, type=Path)
    compare_parser.add_argument("--candidate", required=True, type=Path)
    compare_parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.command == "run":
        sizes = tuple(
            int(value.strip())
            for value in str(args.sizes_mib).split(",")
            if value.strip()
        )
        payload = benchmark(
            sizes_mib=sizes,
            warm_samples=max(1, int(args.warm_samples)),
            repo_root=args.repo_root,
            label=str(args.label),
        )
        _write_json(args.output, payload)
        return 0

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    result = compare(baseline, candidate)
    _write_json(args.output, result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
