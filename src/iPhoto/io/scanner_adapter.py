"""Adapter to bridge legacy scanner calls to the new infrastructure."""

from pathlib import Path
from typing import Iterator, Dict, Any, List, Optional, Callable, Iterable
import queue
import unicodedata
from datetime import datetime, timezone
import logging
import mimetypes

from ..application.interfaces import IMetadataProvider, IThumbnailGenerator
from ..infrastructure.services.metadata_provider import ExifToolMetadataProvider
from ..infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from ..people import initial_face_status
from ..utils.hashutils import compute_file_id
from ..utils.pathutils import should_include
from ..config import DEFAULT_INCLUDE, DEFAULT_EXCLUDE
from ..application.use_cases.scan_album import FileDiscoveryThread

# Instantiate services directly for the adapter (stateless)
_metadata_provider = ExifToolMetadataProvider()
_thumbnail_generator = PillowThumbnailGenerator()
_IMAGE_EXTENSIONS = set(getattr(ExifToolMetadataProvider, "_IMAGE_EXTENSIONS", ()))
_VIDEO_EXTENSIONS = set(getattr(ExifToolMetadataProvider, "_VIDEO_EXTENSIONS", ()))
LOGGER = logging.getLogger(__name__)


def _fallback_row_for_path(root: Path, path: Path) -> Dict[str, Any]:
    """Build a minimal index row when rich metadata extraction fails."""

    stat = path.stat()
    dt_obj = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    mime = mimetypes.guess_type(path.name)[0]
    suffix = path.suffix.lower()

    if suffix in _VIDEO_EXTENSIONS or (mime and mime.startswith("video/")):
        media_type = 1
    elif suffix in _IMAGE_EXTENSIONS or (mime and mime.startswith("image/")):
        media_type = 0
    else:
        media_type = None

    row: Dict[str, Any] = {
        "rel": path.relative_to(root).as_posix(),
        "bytes": stat.st_size,
        "dt": dt_obj.isoformat().replace("+00:00", "Z"),
        "ts": int(stat.st_mtime * 1_000_000),
        "id": f"as_{compute_file_id(path)}",
        "mime": mime,
        "media_type": media_type,
        "aspect_ratio": None,
        "year": dt_obj.year,
        "month": dt_obj.month,
    }
    row["face_status"] = initial_face_status(row)
    return row

def process_media_paths(
    root: Path, image_paths: List[Path], video_paths: List[Path]
) -> Iterator[Dict[str, Any]]:
    """Yield populated index rows for the provided media paths."""

    all_paths = image_paths + video_paths
    if not all_paths:
        return

    # Process in batches
    BATCH_SIZE = 50
    for i in range(0, len(all_paths), BATCH_SIZE):
        batch = all_paths[i : i + BATCH_SIZE]

        # Get metadata
        meta_batch = _metadata_provider.get_metadata_batch(batch)

        # Build lookup
        meta_lookup = {}
        for m in meta_batch:
            src = m.get("SourceFile")
            if src:
                meta_lookup[src] = m
                meta_lookup[unicodedata.normalize('NFC', src)] = m
                meta_lookup[unicodedata.normalize('NFD', src)] = m

        for path in batch:
            try:
                raw_meta = meta_lookup.get(path.as_posix())
                if not raw_meta:
                    raw_meta = meta_lookup.get(unicodedata.normalize('NFC', path.as_posix()))
                if not raw_meta:
                    raw_meta = meta_lookup.get(unicodedata.normalize('NFD', path.as_posix()))

                # Normalize
                row = _metadata_provider.normalize_metadata(root, path, raw_meta or {})
            except Exception as exc:
                try:
                    row = _fallback_row_for_path(root, path)
                except OSError as os_exc:
                    LOGGER.warning(
                        "Skipping %s because metadata extraction failed (%s) and no fallback row could be built (%s)",
                        path,
                        exc,
                        os_exc,
                    )
                    continue
                LOGGER.warning(
                    "Metadata extraction failed for %s; indexing with fallback metadata: %s",
                    path,
                    exc,
                    exc_info=True,
                )

            if row.get("media_type") == 0:
                try:
                    mt = _thumbnail_generator.generate_micro_thumbnail(path)
                except Exception as exc:
                    LOGGER.warning(
                        "Micro-thumbnail generation failed for %s; keeping asset indexed without thumbnail: %s",
                        path,
                        exc,
                        exc_info=True,
                    )
                else:
                    if mt:
                        row["micro_thumbnail"] = mt

            yield row

def scan_album(
    root: Path,
    include_globs: Iterable[str],
    exclude_globs: Iterable[str],
    existing_index: Optional[Dict[str, Dict[str, Any]]] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """Yield index rows for all matching assets in *root*, scanning in parallel."""

    path_queue = queue.Queue(maxsize=1000)
    # FileDiscoveryThread expects list, ensure we pass lists
    discoverer = FileDiscoveryThread(root, path_queue, include=list(include_globs), exclude=list(exclude_globs))
    discoverer.start()

    batch = []
    BATCH_SIZE = 50
    total_processed = 0

    def process_batch_rows(paths: List[Path]) -> Iterator[Dict[str, Any]]:
        # Check cache first to avoid expensive metadata extraction
        paths_to_process = []
        for p in paths:
            rel = p.relative_to(root).as_posix()

            # Check existing index
            cached = None
            if existing_index:
                cached = existing_index.get(rel)
                if not cached:
                    cached = existing_index.get(unicodedata.normalize('NFC', rel))

            if cached:
                try:
                    stat = p.stat()
                    # Validate cache
                    cached_ts = cached.get("ts")
                    current_ts = int(stat.st_mtime * 1_000_000)
                    if cached.get("bytes") == stat.st_size and abs((cached_ts or 0) - current_ts) <= 1_000_000:
                        yield cached
                        continue
                except OSError:
                    pass

            paths_to_process.append(p)

        # Process remaining
        if paths_to_process:
             # Reuse process_media_paths logic but we need to split images/videos if we strictly followed signature,
             # but process_media_paths just joins them.
             yield from process_media_paths(root, paths_to_process, [])

    try:
        if progress_callback:
            progress_callback(0, 0)

        while True:
            try:
                path = path_queue.get(timeout=0.5)
            except queue.Empty:
                if not discoverer.is_alive():
                    break
                continue

            if path is None:
                break

            batch.append(path)
            if len(batch) >= BATCH_SIZE:
                yield from process_batch_rows(batch)
                total_processed += len(batch)
                if progress_callback:
                    progress_callback(total_processed, discoverer.total_found)
                batch = []

        if batch:
            yield from process_batch_rows(batch)
            total_processed += len(batch)
            if progress_callback:
                progress_callback(total_processed, discoverer.total_found)

    finally:
        # Cleanup logic similar to original scanner
        discoverer.stop()

        # Drain queue to allow thread to unblock if it was stuck on put()
        while True:
            try:
                path_queue.get(timeout=0.1)
            except queue.Empty:
                if not discoverer.is_alive():
                    break

        discoverer.join(timeout=1.0)
