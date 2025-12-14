"""Async helpers for loading asset metadata into the asset list model."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QThreadPool, Signal, QTimer

from ..tasks.asset_loader_worker import AssetLoaderSignals, AssetLoaderWorker, compute_asset_rows
from ....cache.index_store import IndexStore
from ....config import WORK_DIR_NAME


class AssetDataLoader(QObject):
    """Wrap :class:`AssetLoaderWorker` to provide a minimal Qt friendly API."""

    chunkReady = Signal(Path, list)
    loadFinished = Signal(Path, bool)
    loadProgress = Signal(Path, int, int)
    error = Signal(Path, str)

    # Threshold for synchronous loading (number of rows).
    # If the index has fewer assets than this, we load synchronously
    # on the UI thread to make small albums appear instantly.
    # 20,000 rows is roughly instantaneous on modern SSDs with SQLite.
    SYNC_LOAD_THRESHOLD: int = 20000

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialise the loader wrapper."""
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._worker: Optional[AssetLoaderWorker] = None
        self._signals: Optional[AssetLoaderSignals] = None
        self._request_id: int = 0

    def is_running(self) -> bool:
        """Return ``True`` while a worker is active."""
        return self._worker is not None

    def current_root(self) -> Optional[Path]:
        """Return the album root handled by the active worker, if any."""
        return self._worker.root if self._worker else None

    def populate_from_cache(
        self,
        root: Path,
        featured: List[Dict[str, object]],
        filter_params: Optional[Dict[str, object]] = None,
    ) -> Optional[Tuple[List[Dict[str, object]], int]]:
        """Return cached rows for *root* when the index file remains lightweight.

        The GUI relies on this helper so that tiny albums appear instantly after
        :meth:`AppFacade.open_album` completes.  The implementation mirrors the
        work performed by :class:`AssetLoaderWorker` while routing all Qt signals
        through :func:`QTimer.singleShot`.  Emitting asynchronously prevents
        listeners—especially :class:`PySide6.QtTest.QSignalSpy`—from missing the
        notification window when they connect right after ``open_album`` returns.

        Parameters
        ----------
        root : Path
            The album root directory to load assets from.
        featured : List[Dict[str, object]]
            List of featured asset metadata dictionaries.
        filter_params : Optional[Dict[str, object]]
            Optional dictionary of filter parameters to restrict the assets loaded.
            Supported keys may include:
                - "rating": int or list of int, filter by asset rating
                - "tags": list of str, filter by asset tags
                - "date_range": tuple of (start_date, end_date), filter by date
                - "search": str, full-text search query
            The exact supported keys depend on the implementation of AssetLoaderWorker.

        Returns
        -------
        Optional[Tuple[List[Dict[str, object]], int]]
            A tuple of (rows, total_count) if the index is small enough to load
            synchronously, or None if it should be loaded asynchronously instead.
        """

        try:
            # We use row count from SQLite instead of file size.
            count = IndexStore(root).count(filter_hidden=True, filter_params=filter_params)
        except Exception:
            count = 0

        if count > self.SYNC_LOAD_THRESHOLD:
            return None

        try:
            rows, total = self.compute_rows(root, featured, filter_params=filter_params)
        except Exception as exc:  # pragma: no cover - surfaced via GUI
            message = str(exc)

            def _emit_error(
                album_root: Path = root,
                error_message: str = message,
            ) -> None:
                """Relay synchronous cache failures once the loop resumes."""

                self.error.emit(album_root, error_message)

            def _emit_failed(album_root: Path = root) -> None:
                """Mirror the worker failure path for cached loads."""

                self.loadFinished.emit(album_root, False)

            QTimer.singleShot(0, _emit_error)
            QTimer.singleShot(0, _emit_failed)
            return None

        def _emit_progress(
            album_root: Path = root,
            total_count: int = total,
        ) -> None:
            """Send a synthetic progress update for cached datasets."""

            self.loadProgress.emit(album_root, total_count, total_count)

        def _emit_success(album_root: Path = root) -> None:
            """Dispatch a success notification on the next event iteration."""

            self.loadFinished.emit(album_root, True)

        QTimer.singleShot(0, _emit_progress)
        QTimer.singleShot(0, _emit_success)
        return rows, total

    def start(
        self,
        root: Path,
        featured: List[Dict[str, object]],
        filter_params: Optional[Dict[str, object]] = None,
    ) -> None:
        """
        Launch a background worker for *root*.

        Parameters
        ----------
        root : Path
            The album root directory to load assets from.
        featured : List[Dict[str, object]]
            List of featured asset metadata dictionaries.
        filter_params : Optional[Dict[str, object]]
            Optional dictionary of filter parameters to restrict the assets loaded.
            Supported keys may include:
                - "rating": int or list of int, filter by asset rating
                - "tags": list of str, filter by asset tags
                - "date_range": tuple of (start_date, end_date), filter by date
                - "search": str, full-text search query
            The exact supported keys depend on the implementation of AssetLoaderWorker.
        """
        if self._worker is not None:
            raise RuntimeError("Loader already running")

        self._request_id += 1
        current_request_id = self._request_id

        signals = AssetLoaderSignals()
        signals.chunkReady.connect(
            lambda r, c: self._handle_chunk_ready(r, c, current_request_id)
        )
        signals.finished.connect(
            lambda r, s: self._handle_finished(r, s, current_request_id)
        )
        signals.progressUpdated.connect(
            lambda r, curr, tot: self._handle_progress(r, curr, tot, current_request_id)
        )
        signals.error.connect(
            lambda r, msg: self._handle_error(r, msg, current_request_id)
        )

        worker = AssetLoaderWorker(root, featured, signals, filter_params=filter_params)
        self._worker = worker
        self._signals = signals
        self._pool.start(worker)

    def cancel(self) -> None:
        """Request cancellation for the active worker."""
        if self._worker is None:
            return
        self._worker.cancel()

    def compute_rows(
        self,
        root: Path,
        featured: List[Dict[str, object]],
        filter_params: Optional[Dict[str, object]] = None,
    ) -> Tuple[List[Dict[str, object]], int]:
        """Synchronously compute asset rows for *root*.

        This is primarily used when the index file is small enough to load on the
        GUI thread without noticeably blocking the interface.  The logic mirrors
        what :class:`AssetLoaderWorker` performs in the background.

        Parameters
        ----------
        root : Path
            The album root directory to load assets from.
        featured : List[Dict[str, object]]
            List of featured asset metadata dictionaries.
        filter_params : Optional[Dict[str, object]]
            Optional dictionary of filter parameters to restrict the assets loaded.
            Supported keys may include:
                - "rating": int or list of int, filter by asset rating
                - "tags": list of str, filter by asset tags
                - "date_range": tuple of (start_date, end_date), filter by date
                - "search": str, full-text search query
            The exact supported keys depend on the implementation of AssetLoaderWorker.

        Returns
        -------
        Tuple[List[Dict[str, object]], int]
            A tuple of (rows, total_count) where rows is the list of asset metadata
            dictionaries and total_count is the total number of assets in the album.
        """

        return compute_asset_rows(root, featured, filter_params=filter_params)

    def _handle_chunk_ready(
        self, root: Path, chunk: List[Dict[str, object]], request_id: int
    ) -> None:
        """Relay chunk notifications from the worker if the request ID matches."""
        if request_id != self._request_id:
            return
        self.chunkReady.emit(root, chunk)

    def _handle_progress(
        self, root: Path, current: int, total: int, request_id: int
    ) -> None:
        """Relay progress updates from the worker if the request ID matches."""
        if request_id != self._request_id:
            return
        self.loadProgress.emit(root, current, total)

    def _handle_finished(self, root: Path, success: bool, request_id: int) -> None:
        """Relay completion notifications and tear down the worker if the request ID matches."""
        if request_id != self._request_id:
            return
        self.loadFinished.emit(root, success)
        self._teardown()

    def _handle_error(self, root: Path, message: str, request_id: int) -> None:
        """Relay worker errors and tear down the worker if the request ID matches."""
        if request_id != self._request_id:
            return
        self.error.emit(root, message)
        self._teardown()

    def _teardown(self) -> None:
        """Release references to worker objects."""
        if self._worker is not None:
            self._worker.signals.deleteLater()
        elif self._signals is not None:
            self._signals.deleteLater()
        self._worker = None
        self._signals = None
