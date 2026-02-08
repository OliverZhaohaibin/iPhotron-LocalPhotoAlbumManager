"""Benchmark scan, load, and thumbnail generation for refactor tracking."""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from iPhoto.cache.index_store import get_global_repository
from iPhoto.config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from iPhoto.io.scanner_adapter import scan_album
from iPhoto.infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from iPhoto.utils.pathutils import should_include

TARGET_SCAN_100K_SECONDS = 300.0  # 5 minutes for 100k files
TARGET_OPEN_100K_SECONDS = 2.0  # 2 seconds to load 100k assets
TARGET_THUMBNAIL_MS = 100.0  # 100ms per thumbnail


@dataclass(frozen=True)
class BenchmarkResult:
    label: str
    elapsed_seconds: float
    count: int

    @property
    def per_item_ms(self) -> float:
        if self.count <= 0:
            return 0.0
        return (self.elapsed_seconds / self.count) * 1000.0

    @property
    def items_per_second(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return self.count / self.elapsed_seconds


def _iter_media_paths(root: Path, include: Iterable[str], exclude: Iterable[str]) -> Iterator[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if not should_include(path, include, exclude, root=root):
            continue
        yield path


def benchmark_scan(root: Path, include: Iterable[str], exclude: Iterable[str]) -> BenchmarkResult:
    start = time.perf_counter()
    count = 0
    for _row in scan_album(root, include, exclude):
        count += 1
    elapsed = time.perf_counter() - start
    return BenchmarkResult(label="scan", elapsed_seconds=elapsed, count=count)


def benchmark_load(root: Path, library_root: Path | None) -> BenchmarkResult:
    db_root = library_root if library_root else root
    repo = get_global_repository(db_root)
    album_path = None
    if library_root:
        try:
            album_path = root.resolve().relative_to(library_root.resolve()).as_posix()
        except (OSError, ValueError):
            album_path = None

    start = time.perf_counter()
    if album_path:
        rows = list(repo.read_album_assets(album_path, include_subalbums=True))
    else:
        rows = list(repo.read_all())
    elapsed = time.perf_counter() - start
    return BenchmarkResult(label="load", elapsed_seconds=elapsed, count=len(rows))


def benchmark_thumbnails(
    root: Path,
    include: Iterable[str],
    exclude: Iterable[str],
    sample_size: int,
) -> BenchmarkResult:
    generator = PillowThumbnailGenerator()
    paths = []
    for path in _iter_media_paths(root, include, exclude):
        paths.append(path)
        if len(paths) >= sample_size:
            break

    start = time.perf_counter()
    processed = 0
    for path in paths:
        if path.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}:
            generator.generate(path, (320, 320))
        else:
            generator.generate_micro_thumbnail(path)
        processed += 1
    elapsed = time.perf_counter() - start
    return BenchmarkResult(label="thumbnail", elapsed_seconds=elapsed, count=processed)


def _estimate_seconds_for_100k(result: BenchmarkResult) -> float:
    rate = result.items_per_second
    if rate <= 0:
        return 0.0
    return 100_000 / rate


def _format_status(label: str, passed: bool) -> str:
    return "PASS" if passed else "WARN"


def _print_result(result: BenchmarkResult) -> None:
    print(
        f"{result.label}: {result.count} items in {result.elapsed_seconds:.2f}s "
        f"({result.items_per_second:.1f} items/s, {result.per_item_ms:.1f} ms/item)"
    )


def run_benchmarks(args: argparse.Namespace) -> None:
    root = Path(args.album_root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Album root does not exist: {root}")

    include = args.include or DEFAULT_INCLUDE
    exclude = args.exclude or DEFAULT_EXCLUDE

    if not args.skip_scan:
        scan_result = benchmark_scan(root, include, exclude)
        _print_result(scan_result)
        est_scan = _estimate_seconds_for_100k(scan_result)
        scan_pass = 0 < est_scan <= TARGET_SCAN_100K_SECONDS
        print(
            f"scan: est 100k in {est_scan:.1f}s (target {TARGET_SCAN_100K_SECONDS:.1f}s) "
            f"[{_format_status('scan', scan_pass)}]"
        )

    if not args.skip_load:
        library_root = Path(args.library_root).expanduser().resolve() if args.library_root else None
        load_result = benchmark_load(root, library_root)
        _print_result(load_result)
        est_load = _estimate_seconds_for_100k(load_result)
        load_pass = 0 < est_load <= TARGET_OPEN_100K_SECONDS
        print(
            f"load: est 100k in {est_load:.1f}s (target {TARGET_OPEN_100K_SECONDS:.1f}s) "
            f"[{_format_status('load', load_pass)}]"
        )

    if not args.skip_thumbnails:
        thumb_result = benchmark_thumbnails(root, include, exclude, args.thumbnail_sample)
        _print_result(thumb_result)
        thumb_pass = 0 < thumb_result.per_item_ms <= TARGET_THUMBNAIL_MS
        print(
            f"thumbnail: {thumb_result.per_item_ms:.1f}ms/item (target {TARGET_THUMBNAIL_MS:.1f}ms) "
            f"[{_format_status('thumbnail', thumb_pass)}]"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "album_root",
        nargs="?",
        default=".",
        help="Album root to benchmark (defaults to current directory)",
    )
    parser.add_argument(
        "--library-root",
        help="Optional library root for global database access",
    )
    parser.add_argument(
        "--thumbnail-sample",
        type=int,
        default=50,
        help="Number of assets to sample for thumbnail benchmark",
    )
    parser.add_argument(
        "--include",
        nargs="*",
        default=None,
        help="Include globs (defaults to project DEFAULT_INCLUDE)",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=None,
        help="Exclude globs (defaults to project DEFAULT_EXCLUDE)",
    )
    parser.add_argument("--skip-scan", action="store_true")
    parser.add_argument("--skip-load", action="store_true")
    parser.add_argument("--skip-thumbnails", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    run_benchmarks(args)


if __name__ == "__main__":
    main()
