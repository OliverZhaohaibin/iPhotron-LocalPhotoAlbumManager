"""Controller for asset loading and ingestion."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING, Set

from PySide6.QtCore import QObject, Signal, QTimer, QThreadPool

from ...tasks.asset_loader_worker import (
    build_asset_entry,
    normalize_featured,
    LiveIngestWorker,
    AssetLoaderSignals,
)
from ..asset_data_loader import AssetDataLoader
from .....utils.pathutils import normalise_rel_value

if TYPE_CHECKING:
    from ...facade import AppFacade
    from .data_repository import AssetRepository

logger = logging.getLogger(__name__)


class AssetIngestionController(QObject):
    """
    Manages the data loading pipeline:
    1. Starts AssetDataLoader
    2. Buffers incoming chunks
    3. Throttles updates to the UI (via batchReady signal)
    """

    # Signals
    batchReady = Signal(list)  # Emits list[dict] of rows ready to be inserted
    loadProgress = Signal(Path, int, int)
    loadFinished = Signal(Path, bool)
    error = Signal(str)

    # Tuning constants for streaming updates
    _STREAM_FLUSH_INTERVAL_MS = 100
    _STREAM_BATCH_SIZE = 100
    _STREAM_FLUSH_THRESHOLD = 2000

    def __init__(self, parent: QObject, repository: "AssetRepository", facade: "AppFacade") -> None:
        super().__init__(parent)
        self._repo = repository
        self._facade = facade
        self._album_root: Optional[Path] = None

        self._data_loader = AssetDataLoader(self)
        self._data_loader.chunkReady.connect(self._on_loader_chunk_ready)
        self._data_loader.loadProgress.connect(self._on_loader_progress)
        self._data_loader.loadFinished.connect(self._on_loader_finished)
        self._data_loader.error.connect(self._on_loader_error)

        # Streaming buffer state
        self._pending_chunks_buffer: List[Dict[str, object]] = []
        self._pending_rels: Set[str] = set()
        self._pending_abs: Set[str] = set()
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(self._STREAM_FLUSH_INTERVAL_MS)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_pending_chunks)
        self._is_first_chunk = True
        self._is_flushing = False

        self._pending_finish_event: Optional[Tuple[Path, bool]] = None
        self._pending_loader_root: Optional[Path] = None
        self._ignore_incoming_chunks: bool = False
        self._active_filter: Optional[str] = None
        self._current_live_worker: Optional[LiveIngestWorker] = None

        # Connect facade signals relevant to ingestion
        self._facade.scanChunkReady.connect(self._on_scan_chunk_ready)

    def set_library_root(self, root: Path) -> None:
        self._data_loader.set_library_root(root)

    def set_active_filter(self, filter_mode: Optional[str]) -> None:
        self._active_filter = filter_mode

    def is_loading(self) -> bool:
        return self._data_loader.is_running()

    def reset(self, root: Path) -> None:
        """Reset state for a new album."""
        if self._data_loader.is_running():
            self._data_loader.cancel()
            self._ignore_incoming_chunks = True
        else:
            self._ignore_incoming_chunks = False

        self._album_root = root
        self._pending_chunks_buffer = []
        self._pending_rels.clear()
        self._pending_abs.clear()
        self._flush_timer.stop()
        self._pending_finish_event = None
        self._is_flushing = False
        self._pending_loader_root = None

        # We don't clear the repo here, the model/facade logic does that before calling reset usually?
        # In AssetListModel.prepare_for_album, it clears rows then calls this (or logic equivalent).
        # We assume the caller clears the repo.

    def start_load(self, album_root: Path) -> None:
        self._album_root = album_root
        if self._data_loader.is_running():
            self._data_loader.cancel()
            self._ignore_incoming_chunks = True
            # Clear buffer immediately
            self._pending_chunks_buffer = []
            self._flush_timer.stop()
            self._is_first_chunk = True
            self._repo.mark_reload_pending()
            return

        self._pending_chunks_buffer = []
        self._pending_rels.clear()
        self._pending_abs.clear()
        self._flush_timer.stop()
        self._pending_finish_event = None
        self._is_first_chunk = True
        self._is_flushing = False

        manifest = self._facade.current_album.manifest if self._facade.current_album else {}
        featured = manifest.get("featured", []) or []

        self._pending_loader_root = self._album_root

        filter_params = {}
        if self._active_filter:
            filter_params["filter_mode"] = self._active_filter

        try:
            self._data_loader.start(self._album_root, featured, filter_params=filter_params)
            self._ignore_incoming_chunks = False

            # Inject live items
            if self._facade.library_manager:
                try:
                    if self._current_live_worker:
                        self._current_live_worker.cancel()
                        self._current_live_worker = None

                    live_items = self._facade.library_manager.get_live_scan_results(relative_to=self._album_root)
                    if live_items:
                        live_signals = AssetLoaderSignals(self)
                        live_signals.chunkReady.connect(self._on_loader_chunk_ready)
                        live_signals.finished.connect(lambda _, __: live_signals.deleteLater())

                        worker = LiveIngestWorker(
                            self._album_root,
                            live_items,
                            featured,
                            live_signals,
                            filter_params=filter_params,
                        )
                        self._current_live_worker = worker
                        QThreadPool.globalInstance().start(worker)
                except Exception as e:
                    logger.error("Failed to inject live scan results: %s", e, exc_info=True)

        except RuntimeError:
            self._repo.mark_reload_pending()
            self._pending_loader_root = None
            return

        self._repo.clear_reload_pending()

    def _on_loader_chunk_ready(self, root: Path, chunk: List[Dict[str, object]]) -> None:
        if self._ignore_incoming_chunks:
            return

        if (
            not self._album_root
            or root != self._album_root
            or not chunk
            or self._pending_loader_root != self._album_root
        ):
            return

        unique_chunk = []
        for row in chunk:
            rel = row.get("rel")
            if not rel:
                continue
            norm_rel = normalise_rel_value(rel)
            abs_val = row.get("abs")
            abs_key = str(abs_val) if abs_val else None

            if (
                norm_rel not in self._repo.row_lookup
                and norm_rel not in self._pending_rels
                and (not abs_key or (
                    self._repo.get_index_by_abs(abs_key) is None
                    and abs_key not in self._pending_abs
                ))
            ):
                unique_chunk.append(row)
                self._pending_rels.add(norm_rel)
                if abs_key:
                    self._pending_abs.add(abs_key)

        if not unique_chunk:
            return

        chunk = unique_chunk

        if self._is_first_chunk:
            self._is_first_chunk = False

            if self._repo.row_count() > 0:
                self._pending_chunks_buffer.extend(chunk)
                if len(self._pending_chunks_buffer) >= self._STREAM_FLUSH_THRESHOLD:
                    self._flush_pending_chunks()
                elif not self._flush_timer.isActive():
                    self._flush_timer.start()
            else:
                # Immediate insert for first chunk if repo is empty
                # We emit batchReady. The model should handle "reset" if needed?
                # Actually, if repo is empty, we just insert.
                # But AssetListModel logic had `beginResetModel` / `endResetModel` for first chunk.
                # "If we already have rows... treat as subsequent"
                # "Else ... beginResetModel ... append_chunk ... endResetModel"

                # To support this, we might need a signal indicating "Reset with this data" vs "Append this data".
                # But typically `beginResetModel` clears everything. Here we are populating.
                # If we use `batchReady`, the model will append.
                # If the model is empty, appending is fine.
                # The original code used `beginResetModel` probably to signal a "clean start" to Views.

                # I'll stick to `batchReady`. The model can decide to reset if it wants, but if repo is empty, insert is fine.
                # Wait, `beginResetModel` forces views to redraw everything. `beginInsertRows` is incremental.
                # For the FIRST chunk, `beginResetModel` is often more performant if we are replacing initial state?
                # But here we assume `prepare_for_album` already cleared the model.

                # But note: `_on_loader_chunk_ready` in original code calls `beginResetModel` only if `_is_first_chunk` AND `row_count == 0`.
                # If I just emit `batchReady`, the model will `beginInsert`.

                self.batchReady.emit(chunk)

                # Cleanup pending
                for row in chunk:
                    rel = row.get("rel")
                    if rel:
                        self._pending_rels.discard(normalise_rel_value(rel))
                    abs_val = row.get("abs")
                    if abs_val:
                        self._pending_abs.discard(str(abs_val))
            return

        self._pending_chunks_buffer.extend(chunk)

        if len(self._pending_chunks_buffer) >= self._STREAM_FLUSH_THRESHOLD:
            self._flush_pending_chunks()
        elif not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending_chunks(self) -> None:
        if self._is_flushing:
            return
        if not self._pending_chunks_buffer:
            return

        self._is_flushing = True
        try:
            batch_size = self._STREAM_BATCH_SIZE
            payload = self._pending_chunks_buffer[:batch_size]

            remainder = self._pending_chunks_buffer[batch_size:]
            self._pending_chunks_buffer = remainder

            if self._pending_chunks_buffer:
                interval = 0 if self._pending_finish_event else self._STREAM_FLUSH_INTERVAL_MS
                self._flush_timer.start(interval)
            else:
                self._flush_timer.stop()

            self.batchReady.emit(payload)

            for row in payload:
                rel = row.get("rel")
                if rel:
                    self._pending_rels.discard(normalise_rel_value(rel))
                abs_val = row.get("abs")
                if abs_val:
                    self._pending_abs.discard(str(abs_val))

            if self._pending_finish_event and not self._pending_chunks_buffer:
                self._finalize_loading(*self._pending_finish_event)

        finally:
            self._is_flushing = False

    def _on_scan_chunk_ready(self, root: Path, chunk: List[Dict[str, object]]) -> None:
        if not self._album_root or not chunk:
            return

        try:
            scan_root = root.resolve()
            view_root = self._album_root.resolve()
        except OSError:
            return

        is_direct_match = (scan_root == view_root)
        is_scan_parent_of_view = (scan_root in view_root.parents)

        if not (is_direct_match or is_scan_parent_of_view):
            return

        manifest = self._facade.current_album.manifest if self._facade.current_album else {}
        featured = manifest.get("featured", []) or []
        featured_set = normalize_featured(featured)

        entries: List[Dict[str, object]] = []
        for row in chunk:
            raw_rel = row.get("rel")
            if not raw_rel:
                continue

            full_path = scan_root / raw_rel

            try:
                view_rel = full_path.relative_to(view_root).as_posix()
            except (ValueError, OSError):
                continue

            if normalise_rel_value(view_rel) in self._repo.row_lookup:
                continue

            adjusted_row = row.copy()
            adjusted_row['rel'] = view_rel

            entry = build_asset_entry(
                view_root, adjusted_row, featured_set
            )

            if entry is not None:
                if self._active_filter == "videos" and not entry.get("is_video"):
                    continue
                if self._active_filter == "live" and not entry.get("is_live"):
                    continue
                if self._active_filter == "favorites" and not entry.get("featured"):
                    continue

                entries.append(entry)

        if entries:
            self.batchReady.emit(entries)

    def _on_loader_progress(self, root: Path, current: int, total: int) -> None:
        if not self._album_root or root != self._album_root:
            return
        self.loadProgress.emit(root, current, total)

    def _on_loader_finished(self, root: Path, success: bool) -> None:
        if self._ignore_incoming_chunks:
            should_restart = self._repo.consume_pending_reload(self._album_root, root)
            self._ignore_incoming_chunks = False
            self._pending_loader_root = None

            self._pending_chunks_buffer = []
            self._pending_rels.clear()
            self._pending_abs.clear()
            self._flush_timer.stop()
            self._pending_finish_event = None

            self.loadFinished.emit(root, success)
            if should_restart:
                QTimer.singleShot(0, lambda: self.start_load(self._album_root))
            return

        if not self._album_root or root != self._album_root:
            should_restart = self._repo.consume_pending_reload(self._album_root, root)
            if should_restart:
                QTimer.singleShot(0, lambda: self.start_load(self._album_root))
            return

        if not self._pending_chunks_buffer:
            self._finalize_loading(root, success)
        else:
            self._pending_finish_event = (root, success)
            self._flush_pending_chunks()

    def _finalize_loading(self, root: Path, success: bool) -> None:
        self._pending_finish_event = None
        self._flush_timer.stop()
        self.loadFinished.emit(root, success)
        self._pending_loader_root = None

        should_restart = self._repo.consume_pending_reload(self._album_root, root)
        if should_restart:
            QTimer.singleShot(0, lambda: self.start_load(self._album_root))

    def _on_loader_error(self, root: Path, message: str) -> None:
        if not self._album_root or root != self._album_root:
            should_restart = self._repo.consume_pending_reload(self._album_root, root)
            self.loadFinished.emit(root, False)
            if should_restart:
                QTimer.singleShot(0, lambda: self.start_load(self._album_root))
            return

        self.error.emit(message)
        self.loadFinished.emit(root, False)

        self._pending_chunks_buffer = []
        self._pending_rels.clear()
        self._pending_abs.clear()
        self._flush_timer.stop()
        self._pending_finish_event = None
        self._pending_loader_root = None

        should_restart = self._repo.consume_pending_reload(self._album_root, root)
        if should_restart:
            QTimer.singleShot(0, lambda: self.start_load(self._album_root))
