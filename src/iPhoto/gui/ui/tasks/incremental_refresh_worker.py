"""Background worker for incremental asset list updates."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QRunnable, Signal

from .asset_loader_worker import compute_asset_rows

LOGGER = logging.getLogger(__name__)


class IncrementalRefreshSignals(QObject):
    """Signal container for :class:`IncrementalRefreshWorker` events."""

    resultsReady = Signal(Path, list)  # list is fresh_rows
    error = Signal(Path, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class IncrementalRefreshWorker(QRunnable):
    """Calculate model updates on a background thread to avoid UI freeze."""

    def __init__(
        self,
        root: Path,
        featured: List[str],
        signals: IncrementalRefreshSignals,
        filter_params: Optional[Dict[str, object]] = None,
        descendant_root: Optional[Path] = None,
        library_root: Optional[Path] = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._root = root
        self._featured = featured
        self._signals = signals
        self._filter_params = filter_params
        self._descendant_root = descendant_root
        self._library_root = library_root

    def run(self) -> None:
        try:
            # 1. Load fresh rows from DB (location resolution occurs in build_asset_entry, called during row processing)
            fresh_rows, _ = compute_asset_rows(
                self._root, self._featured, filter_params=self._filter_params,
                library_root=self._library_root,
            )

            # 2. If a descendant root is involved, merge its fresh data into the current set of rows
            if self._descendant_root and self._descendant_root != self._root:
                self._merge_descendant_rows(fresh_rows)

            # 3. Emit results (let main thread handle diffing to avoid race conditions)
            self._signals.resultsReady.emit(self._root, fresh_rows)

        except Exception as exc:
            LOGGER.error("IncrementalRefreshWorker failed for %s: %s", self._root, exc)
            self._signals.error.emit(self._root, str(exc))

    def _merge_descendant_rows(self, fresh_rows: List[Dict[str, object]]) -> None:
        """Merge fresh rows from the descendant album into the parent's row set."""
        # Import Album locally to avoid circular dependencies.

        from ....models.album import Album
        from ....errors import IPhotoError
        from ....utils.pathutils import normalise_rel_value

        try:
            # Load the descendant's manifest to get the fresh 'featured' list.
            try:
                child_album = Album.open(self._descendant_root)
                child_featured = child_album.manifest.get("featured", [])
            except (IPhotoError, OSError, ValueError) as exc:
                LOGGER.error(
                    "IncrementalRefreshWorker: failed to load manifest for %s: %s",
                    self._descendant_root,
                    exc,
                )
                child_featured = []

            child_rows, _ = compute_asset_rows(
                self._descendant_root, child_featured, filter_params=self._filter_params,
                library_root=self._library_root,
            )

            if child_rows:
                # Map fresh rows by rel for O(1) update
                fresh_lookup = {
                    normalise_rel_value(row.get("rel")): i
                    for i, row in enumerate(fresh_rows)
                }

                rel_prefix = self._descendant_root.relative_to(self._root)
                prefix_str = rel_prefix.as_posix()

                for child_row in child_rows:
                    child_rel = child_row.get("rel")
                    if not child_rel:
                        continue

                    # Adjust child rel to be relative to the parent root
                    child_rel_str = str(child_rel).replace("\\", "/")
                    from pathlib import PurePosixPath
                    adjusted_rel = PurePosixPath(prefix_str, child_rel_str).as_posix()
                    normalized_key = normalise_rel_value(adjusted_rel)

                    if normalized_key in fresh_lookup:
                        merged_row = child_row.copy()
                        merged_row["rel"] = adjusted_rel
                        fresh_rows[fresh_lookup[normalized_key]] = merged_row

        except Exception as exc:
            LOGGER.warning("IncrementalRefreshWorker: descendant merge failed: %s", exc)
