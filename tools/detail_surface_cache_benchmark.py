#!/usr/bin/env python3
"""Benchmark neutral-surface cache I/O without requiring the full GUI runtime."""

from __future__ import annotations

import argparse
import gc
import hashlib
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

try:
    import xxhash as benchmark_xxhash
except ImportError:  # pragma: no cover - production dependency
    benchmark_xxhash = None


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


def _process_metrics() -> tuple[int, int]:
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
            return int(counters.WorkingSetSize), int(counters.PageFaultCount)
        return 0, 0
    page_faults = 0
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        page_faults = int(usage.ru_minflt) + int(usage.ru_majflt)
    except (ImportError, OSError, ValueError):
        pass
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024, page_faults
        except (OSError, ValueError):
            return 0, page_faults
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        rss = value if sys.platform == "darwin" else value * 1024
        return rss, page_faults
    except (ImportError, OSError, ValueError):
        return 0, page_faults


def _rss_bytes() -> int:
    return _process_metrics()[0]


def _consume_image(image: QImage) -> str:
    """Traverse every mapped pixel without calling the store checksum helper."""

    payload = memoryview(image.constBits())[: int(image.sizeInBytes())]
    try:
        if benchmark_xxhash is not None:
            return str(benchmark_xxhash.xxh64(payload).hexdigest())
        return hashlib.blake2b(payload, digest_size=8).hexdigest()
    finally:
        try:
            payload.release()
        except (BufferError, ValueError):
            pass


def _measure_store_hit(
    store: NeutralSurfaceStore,
    request: DetailRenderRequest,
    *,
    size_mib: int,
) -> dict[str, Any]:
    rss_before, faults_before = _process_metrics()
    total_started = time.perf_counter()
    load_started = total_started
    loaded = store.load(request)
    load_ms = (time.perf_counter() - load_started) * 1000.0
    if loaded is None:
        raise RuntimeError(f"surface cache hit failed for {size_mib} MiB")
    consume_started = time.perf_counter()
    digest = _consume_image(loaded.image)
    consume_ms = (time.perf_counter() - consume_started) * 1000.0
    total_ms = (time.perf_counter() - total_started) * 1000.0
    rss_after, faults_after = _process_metrics()
    del loaded
    gc.collect()
    return {
        "load_ms": load_ms,
        "consume_ms": consume_ms,
        "load_to_consumed_ms": total_ms,
        "rss_delta_bytes": max(0, rss_after - rss_before),
        "page_faults": max(0, faults_after - faults_before),
        "consumption_digest": digest,
    }


def _load_and_consume(
    library: Path,
    *,
    size_mib: int,
    revision: int,
) -> dict[str, Any]:
    request = _request(library, size_mib=size_mib, revision=revision)
    store = _store(library)
    checksum_calls = 0
    original_checksum = cache_module._checksum

    def counted_checksum(payload, checksum=original_checksum) -> int:
        nonlocal checksum_calls
        checksum_calls += 1
        return checksum(payload)

    cache_module._checksum = counted_checksum
    try:
        result = _measure_store_hit(store, request, size_mib=size_mib)
        result["checksum_calls"] = checksum_calls
        return result
    finally:
        cache_module._checksum = original_checksum
        close = getattr(store, "close", None)
        if callable(close):
            close()


def _fresh_process_probe(
    library: Path,
    *,
    size_mib: int,
    revision: int,
) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and local tool
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "probe",
            "--library",
            str(library),
            "--size-mib",
            str(size_mib),
            "--revision",
            str(revision),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    return dict(json.loads(completed.stdout))


def _git_sha(repo_root: Path) -> str:
    executable = shutil.which("git")
    if executable is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - resolved git executable only
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
    consume_samples: int = 3,
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

            payload_bytes = int(surface.image.sizeInBytes())
            namespace = "unknown"
            root = getattr(store, "root", None)
            if isinstance(root, Path):
                namespace = root.name
            close = getattr(store, "close", None)
            if callable(close):
                close()
            del surface
            gc.collect()

            fresh_probes = [
                _fresh_process_probe(
                    library,
                    size_mib=size_mib,
                    revision=revision,
                )
                for _sample in range(max(1, consume_samples))
            ]

            checksum_calls = 0
            original_checksum = cache_module._checksum

            def counted_checksum(payload, checksum=original_checksum) -> int:
                nonlocal checksum_calls
                checksum_calls += 1
                return checksum(payload)

            store = _store(library)
            cache_module._checksum = counted_checksum
            try:
                first_probe = _measure_store_hit(store, request, size_mib=size_mib)
                warm_probes: list[dict[str, Any]] = []
                for _sample in range(max(1, warm_samples)):
                    warm_probes.append(
                        _measure_store_hit(store, request, size_mib=size_mib)
                    )
            finally:
                cache_module._checksum = original_checksum
                close = getattr(store, "close", None)
                if callable(close):
                    close()

            digests = {
                str(probe["consumption_digest"])
                for probe in [*fresh_probes, first_probe, *warm_probes]
            }
            if len(digests) != 1:
                raise RuntimeError(
                    f"surface consumption digest changed for {size_mib} MiB"
                )
            fresh_load = [float(probe["load_ms"]) for probe in fresh_probes]
            fresh_consume = [float(probe["consume_ms"]) for probe in fresh_probes]
            fresh_total = [
                float(probe["load_to_consumed_ms"]) for probe in fresh_probes
            ]
            warm_load = [float(probe["load_ms"]) for probe in warm_probes]
            warm_consume = [float(probe["consume_ms"]) for probe in warm_probes]
            warm_total = [
                float(probe["load_to_consumed_ms"]) for probe in warm_probes
            ]
            results.append(
                {
                    "size_mib": size_mib,
                    "payload_bytes": payload_bytes,
                    "namespace": namespace,
                    "write_ms": round(write_ms, 3),
                    "first_hit_ms": round(float(first_probe["load_ms"]), 3),
                    "first_consume_ms": round(float(first_probe["consume_ms"]), 3),
                    "first_load_to_consumed_ms": round(
                        float(first_probe["load_to_consumed_ms"]),
                        3,
                    ),
                    "warm_hit_p50_ms": round(statistics.median(warm_load), 3),
                    "warm_hit_p95_ms": round(_percentile(warm_load, 0.95), 3),
                    "warm_consume_p50_ms": round(statistics.median(warm_consume), 3),
                    "warm_consume_p95_ms": round(
                        _percentile(warm_consume, 0.95),
                        3,
                    ),
                    "warm_load_to_consumed_p50_ms": round(
                        statistics.median(warm_total),
                        3,
                    ),
                    "warm_load_to_consumed_p95_ms": round(
                        _percentile(warm_total, 0.95),
                        3,
                    ),
                    "fresh_process_load_p50_ms": round(
                        statistics.median(fresh_load),
                        3,
                    ),
                    "fresh_process_load_p95_ms": round(
                        _percentile(fresh_load, 0.95),
                        3,
                    ),
                    "fresh_process_consume_p50_ms": round(
                        statistics.median(fresh_consume),
                        3,
                    ),
                    "fresh_process_consume_p95_ms": round(
                        _percentile(fresh_consume, 0.95),
                        3,
                    ),
                    "fresh_process_load_to_consumed_p50_ms": round(
                        statistics.median(fresh_total),
                        3,
                    ),
                    "fresh_process_load_to_consumed_p95_ms": round(
                        _percentile(fresh_total, 0.95),
                        3,
                    ),
                    "fresh_process_page_faults_p95": int(
                        _percentile(
                            [float(probe["page_faults"]) for probe in fresh_probes],
                            0.95,
                        )
                    ),
                    "fresh_process_rss_delta_bytes_p95": int(
                        _percentile(
                            [
                                float(probe["rss_delta_bytes"])
                                for probe in fresh_probes
                            ],
                            0.95,
                        )
                    ),
                    "warm_page_faults_p95": int(
                        _percentile(
                            [float(probe["page_faults"]) for probe in warm_probes],
                            0.95,
                        )
                    ),
                    "warm_rss_delta_bytes_p95": int(
                        _percentile(
                            [float(probe["rss_delta_bytes"]) for probe in warm_probes],
                            0.95,
                        )
                    ),
                    "consumption_digest": digests.pop(),
                    "checksum_calls": checksum_calls,
                    "fresh_process_checksum_calls": sum(
                        int(probe["checksum_calls"]) for probe in fresh_probes
                    ),
                    "python_peak_bytes": int(python_peak),
                    "rss_write_delta_bytes": max(0, rss_after_write - rss_before),
                }
            )
            gc.collect()

    return {
        "schema": 2,
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
        "consume_samples": max(1, consume_samples),
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
        cold_consumed = float(old["fresh_process_load_to_consumed_p95_ms"])
        cold_consumed_limit = cold_consumed + max(cold_consumed * 0.05, 10.0)
        warm_consumed = float(old["warm_load_to_consumed_p95_ms"])
        warm_consumed_limit = warm_consumed + max(warm_consumed * 0.05, 10.0)
        payload = int(row["payload_bytes"])
        checks.extend(
            [
                {
                    "name": f"{size}MiB.trusted_hit_checksum_calls",
                    "actual": int(row["checksum_calls"])
                    + int(row["fresh_process_checksum_calls"]),
                    "limit": 0,
                    "passed": int(row["checksum_calls"]) == 0
                    and int(row["fresh_process_checksum_calls"]) == 0,
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
                {
                    "name": f"{size}MiB.fresh_process_load_to_consumed_p95_ms",
                    "actual": float(row["fresh_process_load_to_consumed_p95_ms"]),
                    "limit": round(cold_consumed_limit, 3),
                    "passed": float(row["fresh_process_load_to_consumed_p95_ms"])
                    <= cold_consumed_limit,
                },
                {
                    "name": f"{size}MiB.warm_load_to_consumed_p95_ms",
                    "actual": float(row["warm_load_to_consumed_p95_ms"]),
                    "limit": round(warm_consumed_limit, 3),
                    "passed": float(row["warm_load_to_consumed_p95_ms"])
                    <= warm_consumed_limit,
                },
            ]
        )
    return {
        "schema": 2,
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
    run_parser.add_argument("--consume-samples", type=int, default=3)
    probe_parser = subparsers.add_parser("probe")
    probe_parser.add_argument("--library", required=True, type=Path)
    probe_parser.add_argument("--size-mib", required=True, type=int)
    probe_parser.add_argument("--revision", required=True, type=int)
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
            consume_samples=max(1, int(args.consume_samples)),
        )
        _write_json(args.output, payload)
        return 0

    if args.command == "probe":
        payload = _load_and_consume(
            args.library,
            size_mib=max(1, int(args.size_mib)),
            revision=max(1, int(args.revision)),
        )
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
        return 0

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    result = compare(baseline, candidate)
    _write_json(args.output, result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
