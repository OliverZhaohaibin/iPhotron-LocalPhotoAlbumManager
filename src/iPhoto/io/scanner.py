"""Directory scanner producing index rows."""

from __future__ import annotations

import mimetypes
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from ..config import EXPORT_DIR_NAME, WORK_DIR_NAME
from ..errors import ExternalToolError, IPhotoError
from ..utils.exiftool import get_metadata_batch
from ..utils.hashutils import file_xxh3
from ..utils.logging import get_logger
from ..utils.pathutils import ensure_work_dir, is_excluded, should_include
from .metadata import read_image_meta_with_exiftool, read_video_meta

_IMAGE_EXTENSIONS = {".heic", ".heif", ".heifs", ".heicf", ".jpg", ".jpeg", ".png"}
_VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".qt"}

LOGGER = get_logger()


class FileDiscoverer(threading.Thread):
    """Background thread that walks the directory and queues files for processing."""

    def __init__(
        self,
        root: Path,
        include_globs: Iterable[str],
        exclude_globs: Iterable[str],
        queue_obj: queue.Queue[Optional[Path]],
    ) -> None:
        super().__init__(name=f"ScannerDiscovery-{root.name}")
        self._root = root
        self._include_globs = list(include_globs)
        self._exclude_globs = list(exclude_globs)
        self._queue = queue_obj
        self._total_found = 0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self.daemon = True

    @property
    def total_found(self) -> int:
        """Return the number of files discovered so far."""
        with self._lock:
            return self._total_found

    def stop(self) -> None:
        """Signal the thread to stop discovery."""
        self._stop_event.set()

    def run(self) -> None:
        """Walk the directory and push valid paths to the queue."""
        try:
            for candidate in self._root.rglob("*"):
                if self._stop_event.is_set():
                    break

                if not candidate.is_file():
                    continue
                # Simple optimization: check path parts before costly globs
                parts = candidate.parts
                if WORK_DIR_NAME in parts or EXPORT_DIR_NAME in parts:
                    continue

                if is_excluded(candidate, self._exclude_globs, root=self._root):
                    continue
                if not should_include(candidate, self._include_globs, self._exclude_globs, root=self._root):
                    continue

                suffix = candidate.suffix.lower()
                if suffix in _IMAGE_EXTENSIONS or suffix in _VIDEO_EXTENSIONS:
                    # Use a timeout so we can periodically check the stop event
                    # if the queue is full and blocking
                    while not self._stop_event.is_set():
                        try:
                            # Put the candidate into the queue, then increment total_found.
                            self._queue.put(candidate, timeout=0.1)
                            with self._lock:
                                self._total_found += 1
                            break
                        except queue.Full:
                            # If queue is full, back off and loop to check stop event
                            continue

        except Exception as exc:
            LOGGER.error("File discovery failed: %s", exc)
        finally:
            # Signal end of discovery
            try:
                self._queue.put(None, timeout=0.5)
            # If the queue is full, we are likely stopping and the consumer may have stopped listening,
            # so it's acceptable to ignore this exception.
            except queue.Full:
                LOGGER.warning("Could not signal end of discovery: queue is full. Consumer may hang waiting for sentinel.")


def gather_media_paths(
    root: Path, include_globs: Iterable[str], exclude_globs: Iterable[str]
) -> Tuple[List[Path], List[Path]]:
    """Collect media files that should be indexed.

    .. deprecated::
       Use :func:`scan_album` for efficient parallel scanning. This function
       is kept for backward compatibility and tests that expect full lists.
    """

    image_paths: List[Path] = []
    video_paths: List[Path] = []

    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue
        if WORK_DIR_NAME in candidate.parts:
            continue
        if EXPORT_DIR_NAME in candidate.parts:
            continue
        if is_excluded(candidate, exclude_globs, root=root):
            continue
        if not should_include(candidate, include_globs, exclude_globs, root=root):
            continue

        suffix = candidate.suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            image_paths.append(candidate)
        elif suffix in _VIDEO_EXTENSIONS:
            video_paths.append(candidate)

    return image_paths, video_paths


def process_media_paths(
    root: Path, image_paths: List[Path], video_paths: List[Path]
) -> Iterator[Dict[str, Any]]:
    """Yield populated index rows for the provided media paths.

    This wraps the new streaming implementation for backward compatibility.
    """
    all_paths = image_paths + video_paths
    # We can use a simple generator to feed the stream processor
    def _feeder() -> Iterator[Path]:
        yield from all_paths

    yield from _process_path_stream(root, _feeder())


def _process_path_stream(
    root: Path,
    path_iterator: Iterator[Path],
    progress_callback: Optional[Callable[[int, int], None]] = None,
    total_provider: Optional[Callable[[], int]] = None,
    batch_size: int = 50,
) -> Iterator[Dict[str, Any]]:
    """
    Internal helper to process a stream of paths in batches.

    Args:
        root (Path): The root directory for scanning, used for context.
        path_iterator (Iterator[Path]): An iterator yielding Path objects to process.
        progress_callback (Optional[Callable[[int, int], None]]): Optional callback function
            that is called with the number of processed items and the total number of items.
            Useful for reporting progress. If None, no progress is reported.
        total_provider (Optional[Callable[[], int]]): Optional function that returns the total
            number of items to process. Used in conjunction with progress_callback to provide
            progress updates. If None, total is unknown.
        batch_size (int): The number of paths to process in each batch. Defaults to 50.

    Yields:
        Dict[str, Any]: Populated index rows for each processed media file.
    """
    batch: List[Path] = []
    processed_count = 0
    last_reported_count = 0

    def _flush_batch() -> Iterator[Dict[str, Any]]:
        nonlocal processed_count, last_reported_count
        if not batch:
            return

        try:
            metadata_payloads = get_metadata_batch(batch)
        except ExternalToolError as exc:
            LOGGER.warning("Batch ExifTool query failed for %s files: %s", len(batch), exc)
            metadata_payloads = []

        metadata_lookup: Dict[Path, Dict[str, Any]] = {}
        for payload in metadata_payloads:
            if not isinstance(payload, dict):
                continue
            source = payload.get("SourceFile")
            if isinstance(source, str):
                source_path = Path(source)
                metadata_lookup[source_path] = payload
                metadata_lookup[source_path.resolve()] = payload

        for path in batch:
            try:
                resolved = path.resolve()
                metadata = metadata_lookup.get(resolved) or metadata_lookup.get(path)
                yield _build_row(root, path, metadata)
                processed_count += 1
            except (IPhotoError, OSError) as exc:
                LOGGER.warning("Could not process file %s: %s", path, exc)
                try:
                    stat = path.stat()
                    yield _build_base_row(root, path, stat)
                    processed_count += 1
                except OSError:
                    continue
            # Throttle updates to every 25 items to avoid flooding the UI event loop
            if progress_callback and total_provider:
                if processed_count - last_reported_count >= 25:
                    progress_callback(processed_count, total_provider())
                    last_reported_count = processed_count

        # Ensure we send a final update for the batch if there were leftovers
        if progress_callback and total_provider and processed_count != last_reported_count:
            progress_callback(processed_count, total_provider())
            last_reported_count = processed_count

    for path in path_iterator:
        batch.append(path)
        if len(batch) >= batch_size:
            yield from _flush_batch()
            batch.clear()

    yield from _flush_batch()


def scan_album(
    root: Path,
    include_globs: Iterable[str],
    exclude_globs: Iterable[str],
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """Yield index rows for all matching assets in *root*, scanning in parallel.

    This starts a background thread to discover files while immediately starting
    to process any files found.
    """

    ensure_work_dir(root, WORK_DIR_NAME)

    # Queue for passing paths from discovery thread to processor
    # We use a finite size to apply backpressure if processing is too slow,
    # though usually discovery is faster.
    path_queue: queue.Queue[Optional[Path]] = queue.Queue(maxsize=1000)

    discoverer = FileDiscoverer(root, include_globs, exclude_globs, path_queue)
    discoverer.start()

    def _queue_iterator() -> Iterator[Path]:
        while True:
            # Check periodically to allow breaking if interrupted
            try:
                path = path_queue.get(timeout=0.5)
            except queue.Empty:
                # If discovery is still alive, keep waiting
                if discoverer.is_alive():
                    continue
                else:
                    # Thread died. One final check to see if it put something
                    # before dying but after our last get().
                    try:
                        path = path_queue.get_nowait()
                        if path is None:
                            return
                        yield path
                        # Continue consuming if there might be more?
                        # No, if thread is dead and we got one item, we should
                        # loop back to consume the rest. But get_nowait only gets one.
                        # The logic "while True" handles subsequent iterations.
                        continue
                    except queue.Empty:
                        # Discovery died without sending None?
                        return

            if path is None:
                return
            yield path

    try:
        # If the caller provided a callback, report (0, 0) immediately so UI can show activity
        if progress_callback:
            progress_callback(0, 0)

        yield from _process_path_stream(
            root,
            _queue_iterator(),
            progress_callback=progress_callback,
            total_provider=lambda: discoverer.total_found
        )
    finally:
        # Ensure proper cleanup even if the generator is closed early (cancellation)
        discoverer.stop()

        # Drain queue to allow thread to unblock if it was stuck on put()
        # The thread checks _stop_event, but only on the next iteration or timeout.
        # Draining helps if it's blocked.
        while discoverer.is_alive():
            try:
                path_queue.get_nowait()
            except queue.Empty:
                break

        discoverer.join(timeout=1.0)


def _build_base_row(root: Path, file_path: Path, stat: Any) -> Dict[str, Any]:
    """Create the common metadata fields shared by images and videos."""

    rel = file_path.relative_to(root).as_posix()
    return {
        "rel": rel,
        "bytes": stat.st_size,
        "dt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "ts": int(stat.st_mtime * 1_000_000),
        "id": f"as_{file_xxh3(file_path)}",
        "mime": mimetypes.guess_type(file_path.name)[0],
    }


def _build_row(
    root: Path,
    file_path: Path,
    metadata_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return an index row for ``file_path``."""

    stat = file_path.stat()
    base_row = _build_base_row(root, file_path, stat)

    suffix = file_path.suffix.lower()
    metadata: Dict[str, Any]

    if suffix in _IMAGE_EXTENSIONS:
        metadata = read_image_meta_with_exiftool(file_path, metadata_override)
    elif suffix in _VIDEO_EXTENSIONS:
        metadata = read_video_meta(file_path, metadata_override)
    else:
        metadata = {}

    for key, value in metadata.items():
        if value is None and key in base_row:
            continue
        base_row[key] = value

    if "dt" in metadata and isinstance(metadata["dt"], str):
        try:
            dt_str = metadata["dt"].replace("Z", "+00:00")
            dt_obj = datetime.fromisoformat(dt_str)
            base_row["ts"] = int(dt_obj.timestamp() * 1_000_000)
        except (ValueError, TypeError):
            pass

    return base_row
