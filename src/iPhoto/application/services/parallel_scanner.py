"""Parallel file scanner using ThreadPoolExecutor for concurrent metadata extraction."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator

from iPhoto.domain.models.core import Asset
from iPhoto.events.bus import EventBus
from iPhoto.events.album_events import ScanProgressEvent
from iPhoto.media_classifier import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

LOGGER = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS: frozenset[str] = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


@dataclass
class ScanResult:
    """Result of a parallel scan operation."""

    assets: list[Asset] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return len(self.assets) + len(self.errors)


class ParallelScanner:
    """Parallel file scanner — uses a thread pool to process files concurrently."""

    def __init__(
        self,
        max_workers: int = 4,
        batch_size: int = 100,
        event_bus: EventBus | None = None,
        scan_file_fn=None,
    ):
        self._max_workers = max_workers
        self._batch_size = batch_size
        self._event_bus = event_bus
        self._scan_file_fn = scan_file_fn or self._default_scan_file

    def scan(self, album_path: Path) -> ScanResult:
        """Scan *album_path* for supported media files in parallel."""
        files = list(self._discover_files(album_path))
        total = len(files)

        results: list[Asset] = []
        errors: list[tuple[Path, str]] = []

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(self._scan_file_fn, f): f for f in files}

            for i, future in enumerate(as_completed(futures)):
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

        # Final progress event
        if self._event_bus and total > 0:
            self._event_bus.publish(
                ScanProgressEvent(
                    processed=total,
                    total=total,
                )
            )

        return ScanResult(assets=results, errors=errors)

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def _discover_files(self, path: Path) -> Generator[Path, None, None]:
        """Yield supported media files using a generator to reduce memory."""
        try:
            for entry in os.scandir(path):
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
