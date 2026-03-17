"""Adapter to bridge legacy scanner calls to the new infrastructure."""

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
import queue
from time import perf_counter
import unicodedata
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

from .. import _native
from ..application.use_cases.scan_album import FileDiscoveryThread
from ..infrastructure.services.metadata_provider import ExifToolMetadataProvider
from ..infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from ..utils.logging import get_logger

# Instantiate services directly for the adapter (stateless)
_metadata_provider = ExifToolMetadataProvider()
_thumbnail_generator = PillowThumbnailGenerator()
LOGGER = get_logger()


@dataclass
class _ScanStageTotals:
    prepare_chunks: int = 0
    prepare_items: int = 0
    prepare_elapsed_s: float = 0.0


def _scan_backend_message() -> tuple[str, str | None]:
    status = _native.get_runtime_status()
    label = _native.runtime_mode_label()
    return label, status.failure_reason


def _log_stage(name: str, elapsed_s: float, *, chunks: int, items: int) -> None:
    suffix = f" (chunks={chunks}, items={items})" if chunks or items else ""
    LOGGER.info("%s finished in %.2fs%s", name, elapsed_s, suffix)


@contextmanager
def _scan_trace() -> Iterator[None]:
    label, reason = _scan_backend_message()
    suffix = f" ({reason})" if reason else ""
    LOGGER.info("Scan backend: %s%s", label, suffix)
    started_at = perf_counter()
    try:
        yield
    finally:
        LOGGER.info("Scan finished (%s) in %.2fs", label, perf_counter() - started_at)


def process_media_paths(
    root: Path,
    image_paths: List[Path],
    video_paths: List[Path],
    *,
    announce_scan: bool = True,
    stage_totals: _ScanStageTotals | None = None,
) -> Iterator[Dict[str, Any]]:
    """Yield populated index rows for the provided media paths."""

    all_paths = image_paths + video_paths
    if not all_paths:
        return

    context = _scan_trace() if announce_scan else nullcontext()
    local_prepare_elapsed = 0.0
    local_prepare_chunks = 0
    local_prepare_items = 0

    with context:
        batch_size = _native.PREPARE_CHUNK_ITEMS
        for index in range(0, len(all_paths), batch_size):
            batch = all_paths[index : index + batch_size]

            prepare_started = perf_counter()
            meta_batch = _metadata_provider.get_metadata_batch(batch)
            meta_lookup = _build_meta_lookup(meta_batch)
            prepared_rows = _prepare_rows_batch(root, batch, meta_lookup)
            local_prepare_elapsed += perf_counter() - prepare_started
            local_prepare_chunks += 1
            local_prepare_items += len(batch)

            for path, row in prepared_rows:
                try:
                    if row.get("media_type") == 0:
                        micro_thumbnail = _thumbnail_generator.generate_micro_thumbnail(path)
                        if micro_thumbnail:
                            row["micro_thumbnail"] = micro_thumbnail
                    yield row
                except Exception:
                    continue

    if stage_totals is not None:
        stage_totals.prepare_elapsed_s += local_prepare_elapsed
        stage_totals.prepare_chunks += local_prepare_chunks
        stage_totals.prepare_items += local_prepare_items

    if announce_scan and local_prepare_items:
        _log_stage(
            "prepare/hash",
            local_prepare_elapsed,
            chunks=local_prepare_chunks,
            items=local_prepare_items,
        )


def scan_album(
    root: Path,
    include_globs: Iterable[str],
    exclude_globs: Iterable[str],
    existing_index: Optional[Dict[str, Dict[str, Any]]] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """Yield index rows for all matching assets in *root*, scanning in parallel."""

    with _scan_trace():
        path_queue = queue.Queue(maxsize=1000)
        discoverer = FileDiscoveryThread(
            root,
            path_queue,
            include=list(include_globs),
            exclude=list(exclude_globs),
        )
        discoverer.start()

        batch: list[Path] = []
        batch_size = _native.PREPARE_CHUNK_ITEMS
        total_processed = 0
        stage_totals = _ScanStageTotals()

        def process_batch_rows(paths: List[Path]) -> Iterator[Dict[str, Any]]:
            paths_to_process: list[Path] = []
            for path in paths:
                rel = path.relative_to(root).as_posix()

                cached = None
                if existing_index:
                    cached = existing_index.get(rel)
                    if not cached:
                        cached = existing_index.get(unicodedata.normalize("NFC", rel))

                if cached:
                    try:
                        stat = path.stat()
                        cached_ts = cached.get("ts")
                        current_ts = int(stat.st_mtime * 1_000_000)
                        if (
                            cached.get("bytes") == stat.st_size
                            and abs((cached_ts or 0) - current_ts) <= 1_000_000
                        ):
                            yield cached
                            continue
                    except OSError:
                        pass

                paths_to_process.append(path)

            if paths_to_process:
                yield from process_media_paths(
                    root,
                    paths_to_process,
                    [],
                    announce_scan=False,
                    stage_totals=stage_totals,
                )

        try:
            if progress_callback:
                progress_callback(0, 0)

            while True:
                try:
                    queued = path_queue.get(timeout=0.5)
                except queue.Empty:
                    if not discoverer.is_alive():
                        break
                    continue

                if queued is None:
                    break

                if isinstance(queued, list):
                    batch.extend(queued)
                else:
                    batch.append(queued)

                while len(batch) >= batch_size:
                    current_batch = batch[:batch_size]
                    del batch[:batch_size]
                    yield from process_batch_rows(current_batch)
                    total_processed += len(current_batch)
                    if progress_callback:
                        progress_callback(total_processed, discoverer.total_found)

            if batch:
                yield from process_batch_rows(batch)
                total_processed += len(batch)
                if progress_callback:
                    progress_callback(total_processed, discoverer.total_found)

        finally:
            discoverer.stop()

            while True:
                try:
                    path_queue.get(timeout=0.1)
                except queue.Empty:
                    if not discoverer.is_alive():
                        break

            discoverer.join(timeout=1.0)

            if (
                hasattr(discoverer, "elapsed_s")
                and hasattr(discoverer, "total_chunks")
                and hasattr(discoverer, "total_found")
            ):
                _log_stage(
                    "discovery",
                    float(getattr(discoverer, "elapsed_s", 0.0)),
                    chunks=int(getattr(discoverer, "total_chunks", 0)),
                    items=int(getattr(discoverer, "total_found", 0)),
                )

            if stage_totals.prepare_items:
                _log_stage(
                    "prepare/hash",
                    stage_totals.prepare_elapsed_s,
                    chunks=stage_totals.prepare_chunks,
                    items=stage_totals.prepare_items,
                )


def _build_meta_lookup(meta_batch: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    meta_lookup: Dict[str, Dict[str, Any]] = {}
    for metadata in meta_batch:
        source = metadata.get("SourceFile")
        if source:
            meta_lookup[source] = metadata
            meta_lookup[unicodedata.normalize("NFC", source)] = metadata
            meta_lookup[unicodedata.normalize("NFD", source)] = metadata
    return meta_lookup


def _prepare_rows_batch(
    root: Path,
    batch: List[Path],
    meta_lookup: Dict[str, Dict[str, Any]],
) -> list[tuple[Path, Dict[str, Any]]]:
    prepare_chunk = getattr(_metadata_provider, "prepare_scan_chunk", None)
    if callable(prepare_chunk):
        prepared_rows = prepare_chunk(root, batch, meta_lookup)
        if prepared_rows is not None:
            return prepared_rows

    rows: list[tuple[Path, Dict[str, Any]]] = []
    for path in batch:
        try:
            raw_meta = meta_lookup.get(path.as_posix())
            if not raw_meta:
                raw_meta = meta_lookup.get(unicodedata.normalize("NFC", path.as_posix()))
            row = _metadata_provider.normalize_metadata(root, path, raw_meta or {})
            rows.append((path, row))
        except Exception:
            continue
    return rows
