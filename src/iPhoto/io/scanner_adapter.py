"""Adapter to bridge legacy scanner calls to the new infrastructure."""

from pathlib import Path
from typing import Iterator, Dict, Any, List, Optional, Callable, Iterable
from io import BytesIO
import os
import queue
import threading
import unicodedata
from datetime import datetime, timezone
import logging
import mimetypes
import time
from PIL import Image

from ..core.raw_processor import is_raw_extension
from ..domain.models.query import ThumbnailReadyResult, ThumbnailState
from ..infrastructure.services.metadata_provider import ExifToolMetadataProvider
from ..infrastructure.services.thumbnail_artifact import (
    publish_thumbnail_artifact,
    thumbnail_revision,
)
from ..infrastructure.services.thumbnail_cache_keys import (
    DEFAULT_THUMBNAIL_SIZE,
    thumbnail_cache_file,
    thumbnail_cache_file_for_key,
    thumbnail_cache_key,
)
from ..infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from ..people import initial_face_status
from ..utils.hashutils import compute_file_id
from ..utils.media_access import media_access
from ..utils.pathutils import ensure_work_dir, should_include
from ..config import (
    ALL_WORK_DIR_NAMES,
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    EXPORT_DIR_NAME,
)

# Instantiate services directly for the adapter (stateless)
_metadata_provider = ExifToolMetadataProvider()
_thumbnail_generator = PillowThumbnailGenerator()
_IMAGE_EXTENSIONS = set(getattr(ExifToolMetadataProvider, "_IMAGE_EXTENSIONS", ()))
_VIDEO_EXTENSIONS = set(getattr(ExifToolMetadataProvider, "_VIDEO_EXTENSIONS", ()))
LOGGER = logging.getLogger(__name__)


def ensure_scan_thumbnail(
    path: Path,
    asset_id: str,
    *,
    thumbnail_cache_dir: Path,
    size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
    refresh_cache: bool = False,
    prefer_cached_micro: bool = False,
    expected_revision: str | None = None,
) -> ThumbnailReadyResult:
    """Return one edit-aware full/micro artifact under the stable path key."""

    with media_access.read(path):
        try:
            revision = expected_revision or thumbnail_revision(path)
            cache_key = thumbnail_cache_key(path, size)
            cache_file = thumbnail_cache_file_for_key(thumbnail_cache_dir, cache_key)
            if not refresh_cache and _cache_file_is_ready(cache_file):
                micro_payload = _generate_micro_from_full_cache(cache_file)
                if micro_payload is not None:
                    return ThumbnailReadyResult(
                        state=ThumbnailState.READY,
                        micro_thumbnail=micro_payload,
                        thumb_cache_key=cache_key,
                        thumb_revision=revision,
                    )
            del prefer_cached_micro
            artifact = publish_thumbnail_artifact(
                path,
                thumbnail_cache_dir,
                size=size,
                expected_revision=revision,
                generator=_thumbnail_generator,
            )
            if artifact is None:
                return ThumbnailReadyResult(
                    state=ThumbnailState.FAILED,
                    thumb_error="thumbnail_unavailable",
                    thumb_revision=revision,
                )
            return ThumbnailReadyResult(
                state=ThumbnailState.READY,
                micro_thumbnail=artifact.micro_thumbnail,
                thumb_cache_key=artifact.cache_key,
                thumb_revision=artifact.revision,
            )
        except Exception as exc:
            LOGGER.warning(
                "Scan thumbnail generation failed for %s (%s): %s",
                path,
                asset_id,
                exc,
                exc_info=True,
            )
            return ThumbnailReadyResult(
                state=ThumbnailState.FAILED,
                thumb_error=f"{type(exc).__name__}: {exc}",
                thumb_revision=expected_revision,
            )


def _generate_micro_from_full_cache(cache_file: Path) -> bytes | None:
    """Derive the mandatory micro layer from an already-rendered 512 thumbnail."""

    try:
        with Image.open(cache_file) as image:
            image.thumbnail((16, 16), Image.Resampling.BICUBIC)
            if image.mode != "RGB":
                image = image.convert("RGB")
            payload = BytesIO()
            image.save(payload, format="JPEG", quality=75)
            return payload.getvalue()
    except (OSError, ValueError):
        LOGGER.debug("Failed to derive micro thumbnail from %s", cache_file, exc_info=True)
        return None


def _cache_file_is_ready(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _has_indexed_geometry(row: Dict[str, Any]) -> bool:
    try:
        return int(row.get("w") or 0) > 0 and int(row.get("h") or 0) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def _has_indexed_orientation(row: Dict[str, Any]) -> bool:
    try:
        return 1 <= int(row.get("image_orientation") or 0) <= 8
    except (TypeError, ValueError, OverflowError):
        return False


def _default_thumbnail_cache_dir(root: Path) -> Path:
    return ensure_work_dir(root) / "cache" / "thumbs"


class FileDiscoveryThread(threading.Thread):
    """Discover media paths for the filesystem scanner."""

    def __init__(
        self,
        root: Path,
        queue_obj: queue.Queue,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> None:
        super().__init__(name=f"ScannerDiscovery-{root.name}")
        self._root = Path(root)
        self._queue = queue_obj
        self._include = include or list(DEFAULT_INCLUDE)
        self._exclude = exclude or list(DEFAULT_EXCLUDE)
        self._stop_event = threading.Event()
        self.total_found = 0
        self.daemon = True

    def run(self) -> None:
        try:
            reserved_names = {
                *[name.casefold() for name in ALL_WORK_DIR_NAMES],
                EXPORT_DIR_NAME.casefold(),
            }
            for dirpath, dirnames, filenames in os.walk(self._root):
                if self._stop_event.is_set():
                    break
                dirnames[:] = [
                    name for name in dirnames if name.casefold() not in reserved_names
                ]
                for name in filenames:
                    if self._stop_event.is_set():
                        break
                    candidate = Path(dirpath) / name
                    if should_include(
                        candidate,
                        self._include,
                        self._exclude,
                        root=self._root,
                    ):
                        self._queue.put(candidate)
                        self.total_found += 1
        finally:
            self._queue.put(None)

    def stop(self) -> None:
        self._stop_event.set()


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
        "source_mtime_ns": int(stat.st_mtime_ns),
        "image_orientation": 1,
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
    root: Path,
    image_paths: List[Path],
    video_paths: List[Path],
    *,
    thumbnail_cache_dir: Path | None = None,
) -> Iterator[Dict[str, Any]]:
    """Yield populated index rows for the provided media paths."""

    all_paths = image_paths + video_paths
    if not all_paths:
        return
    resolved_thumbnail_cache_dir = thumbnail_cache_dir or _default_thumbnail_cache_dir(root)

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

            thumbnail = ensure_scan_thumbnail(
                path,
                str(row.get("id") or path),
                thumbnail_cache_dir=resolved_thumbnail_cache_dir,
                refresh_cache=True,
            )
            row["thumbnail_state"] = thumbnail.state.value
            if thumbnail.micro_thumbnail is not None:
                row["micro_thumbnail"] = thumbnail.micro_thumbnail
            if thumbnail.thumb_cache_key:
                row["thumb_cache_key"] = thumbnail.thumb_cache_key
                row["thumb_updated_at"] = _utc_ms()
            if thumbnail.thumb_revision:
                row["thumb_revision"] = thumbnail.thumb_revision
            if thumbnail.thumb_error:
                row["thumb_error"] = thumbnail.thumb_error

            yield row

def scan_album(
    root: Path,
    include_globs: Iterable[str],
    exclude_globs: Iterable[str],
    existing_index: Optional[Dict[str, Dict[str, Any]]] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    thumbnail_cache_dir: Path | None = None,
) -> Iterator[Dict[str, Any]]:
    """Yield index rows for all matching assets in *root*, scanning in parallel."""

    path_queue = queue.Queue(maxsize=1000)
    # FileDiscoveryThread expects list, ensure we pass lists
    discoverer = FileDiscoveryThread(
        root,
        path_queue,
        include=list(include_globs),
        exclude=list(exclude_globs),
    )
    discoverer.start()

    batch = []
    BATCH_SIZE = 50
    total_processed = 0
    resolved_thumbnail_cache_dir = thumbnail_cache_dir or _default_thumbnail_cache_dir(root)

    def process_batch_rows(paths: List[Path]) -> Iterator[Dict[str, Any]]:
        # Check cache first to avoid expensive metadata extraction
        paths_to_process = []
        metadata_only_backfill: list[tuple[Path, Dict[str, Any]]] = []
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
                    source_unchanged = (
                        cached.get("bytes") == stat.st_size
                        and abs((cached_ts or 0) - current_ts) <= 1_000_000
                    )
                    if source_unchanged:
                        cached = dict(cached)
                        cached["source_mtime_ns"] = int(stat.st_mtime_ns)
                        needs_metadata_backfill = (
                            is_raw_extension(p.suffix)
                            and not _has_indexed_geometry(cached)
                        ) or (
                            p.suffix.lower() in _IMAGE_EXTENSIONS
                            and not _has_indexed_orientation(cached)
                        )
                        if needs_metadata_backfill:
                            metadata_only_backfill.append((p, cached))
                            continue

                        indexed_revision = str(
                            cached.get("thumb_revision") or ""
                        ).strip()
                        revision = thumbnail_revision(p)
                        cached["thumb_revision"] = revision
                        if indexed_revision != revision and (
                            indexed_revision or p.with_suffix(".ipo").is_file()
                        ):
                            yield _refresh_cached_thumbnail(
                                p,
                                cached,
                                resolved_thumbnail_cache_dir,
                                expected_revision=revision,
                                refresh_cache=True,
                            )
                            continue
                        if _cached_thumbnail_ready(
                            p,
                            cached,
                            resolved_thumbnail_cache_dir,
                        ):
                            yield cached
                        else:
                            yield _refresh_cached_thumbnail(
                                p,
                                cached,
                                resolved_thumbnail_cache_dir,
                                expected_revision=revision,
                            )
                        continue
                except OSError:
                    pass

            paths_to_process.append(p)

        if metadata_only_backfill:
            yield from _refresh_cached_metadata_rows(
                root,
                metadata_only_backfill,
                resolved_thumbnail_cache_dir,
            )

        # Process remaining
        if paths_to_process:
            # Reuse process_media_paths logic; it treats image/video paths the same here.
            yield from process_media_paths(
                root,
                paths_to_process,
                [],
                thumbnail_cache_dir=resolved_thumbnail_cache_dir,
            )

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


def _cached_thumbnail_ready(
    path: Path,
    cached: Dict[str, Any],
    thumbnail_cache_dir: Path,
) -> bool:
    if cached.get("thumbnail_state") != ThumbnailState.READY.value:
        return False
    cache_key = cached.get("thumb_cache_key")
    if not isinstance(cache_key, str) or not cache_key.strip():
        return False
    expected_key = thumbnail_cache_key(path, DEFAULT_THUMBNAIL_SIZE)
    if cache_key != expected_key:
        return False
    return _cache_file_is_ready(thumbnail_cache_file(thumbnail_cache_dir, path))


def _refresh_cached_thumbnail(
    path: Path,
    cached: Dict[str, Any],
    thumbnail_cache_dir: Path,
    *,
    expected_revision: str | None = None,
    refresh_cache: bool = True,
) -> Dict[str, Any]:
    row = dict(cached)
    thumbnail = ensure_scan_thumbnail(
        path,
        str(row.get("id") or path),
        thumbnail_cache_dir=thumbnail_cache_dir,
        refresh_cache=refresh_cache,
        expected_revision=expected_revision,
    )
    row["thumbnail_state"] = thumbnail.state.value
    row.pop("thumb_error", None)
    if thumbnail.micro_thumbnail is not None:
        row["micro_thumbnail"] = thumbnail.micro_thumbnail
    if thumbnail.thumb_cache_key:
        row["thumb_cache_key"] = thumbnail.thumb_cache_key
        row["thumb_updated_at"] = _utc_ms()
    if thumbnail.thumb_revision:
        row["thumb_revision"] = thumbnail.thumb_revision
    if thumbnail.thumb_error:
        row["thumb_error"] = thumbnail.thumb_error
        if str(cached.get("thumb_cache_key") or "").strip():
            row["thumbnail_state"] = ThumbnailState.STALE.value
            row["micro_thumbnail"] = None
        else:
            row.pop("thumb_cache_key", None)
    return row


_THUMBNAIL_ROW_FIELDS = (
    "thumbnail_state",
    "micro_thumbnail",
    "thumb_cache_key",
    "thumb_updated_at",
    "thumb_error",
    "thumb_revision",
)


def _refresh_cached_metadata_rows(
    root: Path,
    items: list[tuple[Path, Dict[str, Any]]],
    thumbnail_cache_dir: Path,
) -> Iterator[Dict[str, Any]]:
    """Backfill metadata while preserving current stable-path thumbnail state."""

    paths = [path for path, _cached in items]
    metadata = _metadata_provider.get_metadata_batch(paths)
    lookup: dict[str, dict[str, Any]] = {}
    for raw in metadata:
        source = raw.get("SourceFile")
        if source:
            lookup[unicodedata.normalize("NFC", str(source))] = raw
            lookup[unicodedata.normalize("NFD", str(source))] = raw

    for path, cached in items:
        raw = (
            lookup.get(unicodedata.normalize("NFC", path.as_posix()))
            or lookup.get(unicodedata.normalize("NFD", path.as_posix()))
            or {}
        )
        try:
            refreshed = _metadata_provider.normalize_metadata(root, path, raw)
        except Exception:
            LOGGER.warning("Metadata-only backfill failed for %s", path, exc_info=True)
            refreshed = dict(cached)
        for field in _THUMBNAIL_ROW_FIELDS:
            if field in cached:
                refreshed[field] = cached[field]
            else:
                refreshed.pop(field, None)
        try:
            refreshed["source_mtime_ns"] = int(path.stat().st_mtime_ns)
        except OSError:
            pass

        indexed_revision = str(cached.get("thumb_revision") or "").strip()
        revision = thumbnail_revision(path)
        refreshed["thumb_revision"] = revision
        if indexed_revision != revision and (
            indexed_revision or path.with_suffix(".ipo").is_file()
        ):
            refreshed = _refresh_cached_thumbnail(
                path,
                refreshed,
                thumbnail_cache_dir,
                expected_revision=revision,
                refresh_cache=True,
            )
        yield refreshed


def _utc_ms() -> int:
    return int(time.time() * 1000)
