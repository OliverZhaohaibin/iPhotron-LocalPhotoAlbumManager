"""Parallel file scanner with CPU-aware resource management.

Uses :class:`ThreadPoolExecutor` for concurrent metadata extraction while
carefully limiting CPU pressure so the UI thread is never starved:

* Worker count defaults to ``max(1, os.cpu_count() // 2)`` — half the
  available cores — leaving headroom for the main / UI thread.
* A configurable *inter-batch sleep* (``yield_interval``) voluntarily
  yields the GIL between batches, giving the event-loop a scheduling
  window even when all workers are busy.
* Progress events are published at ``batch_size`` intervals so the UI
  can display a live progress bar via streaming updates.
* A *cancelled* flag allows the caller (or the UI) to abort a scan
  mid-flight without waiting for all futures to drain.
* ``scan_streaming`` returns a **generator** of partial
  :class:`ScanResult` batches, enabling the UI to display assets as
  they become available rather than waiting for the full scan to finish.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Generator, Optional

from iPhoto.domain.models.core import Asset
from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import ScanProgressEvent
from iPhoto.media_classifier import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

LOGGER = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS: frozenset[str] = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def _default_max_workers() -> int:
    """Use at most half the CPU cores so the UI thread is never starved."""
    cpus = os.cpu_count() or 2
    return max(1, cpus // 2)


@dataclass
class ScanResult:
    """Result of a parallel scan operation."""

    assets: list[Asset] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return len(self.assets) + len(self.errors)


class ParallelScanner:
    """Parallel file scanner — uses a thread pool to process files concurrently.

    .. note::

       A single :class:`ParallelScanner` instance is **not safe** for
       concurrent use from multiple threads.  The internal ``_cancelled``
       flag is shared, so calling :meth:`cancel` would affect all in-flight
       scans and each scan's initial ``clear()`` could race with other
       scans.  Create separate instances if you need concurrent scans.

    Parameters
    ----------
    max_workers:
        Thread-pool size.  Defaults to ``cpu_count // 2`` to protect the
        UI thread.
    batch_size:
        Number of files between progress event publications.
    event_bus:
        Optional event bus for progress notifications.
    scan_file_fn:
        Callable that processes a single file; injected by the caller.
    yield_interval:
        Seconds to sleep after each batch, giving the main thread a chance
        to process UI events.  ``0`` disables yielding.
    """

    def __init__(
        self,
        max_workers: int | None = None,
        batch_size: int = 100,
        event_bus: EventBus | None = None,
        scan_file_fn: Optional[Callable[[Path], Asset | None]] = None,
        yield_interval: float = 0.005,
    ):
        self._max_workers = max_workers if max_workers is not None else _default_max_workers()
        self._batch_size = batch_size
        self._event_bus = event_bus
        self._scan_file_fn = scan_file_fn or self._default_scan_file
        self._yield_interval = yield_interval
        self._cancelled = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Signal the scanner to abort as soon as possible."""
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def scan(self, album_path: Path) -> ScanResult:
        """Scan *album_path* for supported media files in parallel."""
        self._cancelled.clear()

        files = list(self._discover_files(album_path))
        total = len(files)

        results: list[Asset] = []
        errors: list[tuple[Path, str]] = []

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(self._scan_file_fn, f): f for f in files}

            for i, future in enumerate(as_completed(futures)):
                if self._cancelled.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                path = futures[future]
                try:
                    asset = future.result()
                    if asset is not None:
                        results.append(asset)
                except Exception as e:
                    errors.append((path, str(e)))

                # Publish progress events at batch intervals
                if self._event_bus and (i + 1) % self._batch_size == 0:
                    self._event_bus.publish(
                        ScanProgressEvent(
                            processed=i + 1,
                            total=total,
                        )
                    )
                    # Yield CPU to main / UI thread between batches
                    if self._yield_interval > 0:
                        time.sleep(self._yield_interval)

        # Final progress event
        if self._event_bus and total > 0 and not self._cancelled.is_set():
            self._event_bus.publish(
                ScanProgressEvent(
                    processed=total,
                    total=total,
                )
            )

        return ScanResult(assets=results, errors=errors)

    def scan_streaming(
        self, album_path: Path
    ) -> Generator[ScanResult, None, None]:
        """Scan with **streaming** — yields partial results after each batch.

        This allows the UI to display assets incrementally instead of waiting
        for the entire scan to complete.
        """
        self._cancelled.clear()

        files = list(self._discover_files(album_path))
        total = len(files)
        batch_assets: list[Asset] = []
        batch_errors: list[tuple[Path, str]] = []

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(self._scan_file_fn, f): f for f in files}

            for i, future in enumerate(as_completed(futures)):
                if self._cancelled.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                path = futures[future]
                try:
                    asset = future.result()
                    if asset is not None:
                        batch_assets.append(asset)
                except Exception as e:
                    batch_errors.append((path, str(e)))

                # Yield a partial result after each batch
                if (i + 1) % self._batch_size == 0:
                    if self._event_bus:
                        self._event_bus.publish(
                            ScanProgressEvent(processed=i + 1, total=total)
                        )
                    yield ScanResult(
                        assets=list(batch_assets), errors=list(batch_errors)
                    )
                    batch_assets.clear()
                    batch_errors.clear()
                    # Yield CPU to main / UI thread
                    if self._yield_interval > 0:
                        time.sleep(self._yield_interval)

        # Yield remaining items
        if batch_assets or batch_errors:
            yield ScanResult(assets=batch_assets, errors=batch_errors)

        if self._event_bus and total > 0 and not self._cancelled.is_set():
            self._event_bus.publish(
                ScanProgressEvent(processed=total, total=total)
            )

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def _discover_files(self, path: Path) -> Generator[Path, None, None]:
        """Yield supported media files using a generator to reduce memory."""
        try:
            for entry in os.scandir(path):
                if self._cancelled.is_set():
                    return
                if entry.is_file(follow_symlinks=False) and self._is_supported(entry.name):
                    yield Path(entry.path)
                elif entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                    yield from self._discover_files(Path(entry.path))
        except PermissionError:
            LOGGER.warning("Permission denied: %s", path)

    @staticmethod
    def _is_supported(filename: str) -> bool:
        _, _, ext = filename.rpartition(".")
        return f".{ext.lower()}" in _SUPPORTED_EXTENSIONS if ext else False

    # ------------------------------------------------------------------
    # Default scan stub (to be replaced by caller)
    # ------------------------------------------------------------------

    @staticmethod
    def _default_scan_file(path: Path) -> Asset | None:
        """Placeholder — callers should inject a real scan function."""
        return None
