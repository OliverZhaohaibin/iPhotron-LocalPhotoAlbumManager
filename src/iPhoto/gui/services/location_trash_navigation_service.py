"""GUI transport adapter for Location and Recently Deleted navigation flows."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

if TYPE_CHECKING:
    from ...library.runtime_controller import LibraryRuntimeController


class _LocationAssetsSignals(QObject):
    finished = Signal(int, Path, list)
    error = Signal(int, Path, str)


class _LocationAssetsWorker(QRunnable):
    def __init__(
        self,
        *,
        serial: int,
        root: Path,
        load_assets: Callable[[], list],
        signals: _LocationAssetsSignals,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._serial = int(serial)
        self._root = Path(root)
        self._load_assets = load_assets
        self._signals = signals

    def run(self) -> None:  # pragma: no cover - background Qt task
        try:
            assets = list(self._load_assets())
        except Exception as exc:  # noqa: BLE001 - best-effort UI transport
            self._signals.error.emit(self._serial, self._root, str(exc))
            return
        self._signals.finished.emit(self._serial, self._root, assets)


class _TrashCleanupSignals(QObject):
    finished = Signal()


class _TrashCleanupWorker(QRunnable):
    def __init__(
        self,
        *,
        cleanup: Callable[[], int],
        signals: _TrashCleanupSignals,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._cleanup = cleanup
        self._signals = signals

    def run(self) -> None:  # pragma: no cover - background Qt task
        try:
            self._cleanup()
        finally:
            self._signals.finished.emit()


class LocationTrashNavigationService(QObject):
    """Own background transport and request state for Location/Trash flows."""

    locationAssetsLoaded = Signal(int, Path, list)
    errorRaised = Signal(str)

    _TRASH_CLEANUP_THROTTLE_SEC = 300.0

    def __init__(
        self,
        *,
        library_manager_getter: Callable[[], "LibraryRuntimeController | None"],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._library_manager_getter = library_manager_getter
        self._thread_pool = QThreadPool.globalInstance()
        self._location_request_serial = 0
        self._location_signals: dict[int, _LocationAssetsSignals] = {}
        self._trash_cleanup_running = False
        self._trash_cleanup_lock = threading.Lock()
        self._last_trash_cleanup_at: float | None = None
        self._trash_cleanup_signals: _TrashCleanupSignals | None = None

    def prepare_recently_deleted(self) -> Path | None:
        """Return the deleted-items root and trigger best-effort cleanup."""

        library = self._library_manager()
        if library is None:
            return None
        try:
            deleted_root = library.ensure_deleted_directory()
        except Exception as exc:  # noqa: BLE001 - surface to GUI
            self.errorRaised.emit(str(exc))
            return None
        self._schedule_trash_cleanup(library, deleted_root)
        return deleted_root

    def request_location_assets(self) -> tuple[int, Path] | None:
        """Load geotagged assets in the background and return the request token."""

        library = self._library_manager()
        if library is None:
            return None
        root = library.root()
        if root is None:
            return None

        self._location_request_serial += 1
        serial = self._location_request_serial
        signals = _LocationAssetsSignals()
        signals.finished.connect(self._handle_location_assets_finished)
        signals.error.connect(self._handle_location_assets_error)
        self._location_signals[serial] = signals
        self._thread_pool.start(
            _LocationAssetsWorker(
                serial=serial,
                root=root,
                load_assets=lambda: list(self._load_location_assets(library)),
                signals=signals,
            )
        )
        return serial, root

    def _handle_location_assets_finished(
        self,
        serial: int,
        root: Path,
        assets: list,
    ) -> None:
        signals = self._location_signals.pop(int(serial), None)
        if signals is not None:
            signals.deleteLater()
        if int(serial) != self._location_request_serial:
            return
        self.locationAssetsLoaded.emit(int(serial), Path(root), list(assets))

    def _handle_location_assets_error(
        self,
        serial: int,
        _root: Path,
        message: str,
    ) -> None:
        signals = self._location_signals.pop(int(serial), None)
        if signals is not None:
            signals.deleteLater()
        if int(serial) != self._location_request_serial:
            return
        self.errorRaised.emit(message)

    def _schedule_trash_cleanup(
        self,
        library: "LibraryRuntimeController",
        trash_root: Path,
    ) -> None:
        with self._trash_cleanup_lock:
            should_start = (
                not self._trash_cleanup_running and self._should_run_trash_cleanup()
            )
            if not should_start:
                return
            self._trash_cleanup_running = True
            self._last_trash_cleanup_at = time.monotonic()

        signals = _TrashCleanupSignals()
        signals.finished.connect(self._handle_trash_cleanup_finished)
        self._trash_cleanup_signals = signals
        self._thread_pool.start(
            _TrashCleanupWorker(
                cleanup=lambda: self._cleanup_deleted_index(library, trash_root),
                signals=signals,
            )
        )

    def _handle_trash_cleanup_finished(self) -> None:
        with self._trash_cleanup_lock:
            self._trash_cleanup_running = False
        signals = self._trash_cleanup_signals
        self._trash_cleanup_signals = None
        if signals is not None:
            signals.deleteLater()

    def _should_run_trash_cleanup(self) -> bool:
        if self._last_trash_cleanup_at is None:
            return True
        return (
            time.monotonic() - self._last_trash_cleanup_at
        ) >= self._TRASH_CLEANUP_THROTTLE_SEC

    def _load_location_assets(self, library: "LibraryRuntimeController") -> list:
        location_service = getattr(library, "location_service", None)
        list_geotagged_assets = getattr(
            location_service,
            "list_geotagged_assets",
            None,
        )
        if callable(list_geotagged_assets):
            return list(list_geotagged_assets())
        raise RuntimeError(
            "Active library session is unavailable; location queries require "
            "a bound LibrarySession."
        )

    def _cleanup_deleted_index(self, library: "LibraryRuntimeController", trash_root: Path) -> int:
        lifecycle_service = getattr(library, "asset_lifecycle_service", None)
        cleanup_deleted_index = getattr(
            lifecycle_service,
            "cleanup_deleted_index",
            None,
        )
        if callable(cleanup_deleted_index):
            return int(cleanup_deleted_index(trash_root))
        raise RuntimeError(
            "Active library session is unavailable; trash cleanup requires "
            "a bound LibrarySession."
        )

    def _library_manager(self) -> "LibraryRuntimeController | None":
        return self._library_manager_getter()
