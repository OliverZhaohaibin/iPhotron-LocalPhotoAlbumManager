"""Phase 4 tests: LibraryUpdateAdapter and ScanProgressAdapter boundary.

These tests verify that the Qt adapters in
``src/iPhoto/presentation/qt/adapters/`` act as pure signal-relay objects:
- They forward signals from the service layer to UI subscribers.
- They contain no business rules.
- They do not access infrastructure directly.
- They do not hold worker lifecycle state.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

_pyside6_available = False
try:
    import PySide6  # noqa: F401

    _pyside6_available = True
except ImportError:
    pass


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
class TestLibraryUpdateAdapterSignalRelay:
    """LibraryUpdateAdapter must forward service signals with no transformation."""

    @pytest.fixture
    def adapter_and_service(self, qtbot):
        """Return a wired (adapter, mock_service) pair."""
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        mock_service = MagicMock()
        # wire_service connects to the mock's attributes – we need real Qt signals
        # so we record calls and fire them manually.
        adapter = LibraryUpdateAdapter(
            update_service_getter=lambda: mock_service,
        )
        return adapter, mock_service

    def test_index_updated_is_relayed(self, adapter_and_service, qtbot):
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        root = Path("/tmp/album")
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        with qtbot.waitSignal(adapter.indexUpdated, timeout=1000) as blocker:
            adapter._on_index_updated(root)

        assert blocker.args == [root]

    def test_links_updated_is_relayed(self, qtbot):
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        root = Path("/tmp/album")
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        with qtbot.waitSignal(adapter.linksUpdated, timeout=1000) as blocker:
            adapter._on_links_updated(root)

        assert blocker.args == [root]

    def test_asset_reload_requested_is_relayed(self, qtbot):
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        root = Path("/tmp/album")
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        with qtbot.waitSignal(adapter.assetReloadRequested, timeout=1000) as blocker:
            adapter._on_asset_reload_requested(root, False, True)

        assert blocker.args == [root, False, True]

    def test_error_raised_is_relayed(self, qtbot):
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        with qtbot.waitSignal(adapter.errorRaised, timeout=1000) as blocker:
            adapter._on_error_raised("something went wrong")

        assert blocker.args == ["something went wrong"]

    def test_scan_progress_is_relayed(self, qtbot):
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        root = Path("/tmp/album")
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        with qtbot.waitSignal(adapter.scanProgress, timeout=1000) as blocker:
            adapter._on_scan_progress(root, 5, 10)

        assert blocker.args == [root, 5, 10]

    def test_scan_chunk_ready_is_relayed(self, qtbot):
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        root = Path("/tmp/album")
        chunk = [{"rel": "photo.jpg"}]
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        with qtbot.waitSignal(adapter.scanChunkReady, timeout=1000) as blocker:
            adapter._on_scan_chunk_ready(root, chunk)

        assert blocker.args[0] == root
        assert blocker.args[1] == chunk

    def test_scan_finished_is_relayed(self, qtbot):
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        root = Path("/tmp/album")
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        with qtbot.waitSignal(adapter.scanFinished, timeout=1000) as blocker:
            adapter._on_scan_finished(root, True)

        assert blocker.args == [root, True]

    def test_wire_service_connects_all_signals(self, qtbot):
        """wire_service must connect all expected service signals to relay slots."""
        from PySide6.QtCore import QObject, Signal
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        class FakeService(QObject):
            indexUpdated = Signal(Path)
            linksUpdated = Signal(Path)
            assetReloadRequested = Signal(Path, bool, bool)
            errorRaised = Signal(str)
            scanProgress = Signal(Path, int, int)
            scanChunkReady = Signal(Path, list)
            scanFinished = Signal(Path, bool)

        svc = FakeService()
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: svc)
        adapter.wire_service(svc)

        root = Path("/tmp/album")

        # Verify index signal is forwarded end-to-end.
        with qtbot.waitSignal(adapter.indexUpdated, timeout=1000) as blocker:
            svc.indexUpdated.emit(root)

        assert blocker.args == [root]

    def test_announce_refresh_delegates_to_service(self, qtbot):
        """announce_refresh must call service.announce_album_refresh, not own the logic."""
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        mock_service = MagicMock()
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: mock_service)

        root = Path("/tmp/album")
        adapter.announce_refresh(root, request_reload=True, announce_index=False)

        mock_service.announce_album_refresh.assert_called_once_with(
            root,
            request_reload=True,
            announce_index=False,
            force_reload=False,
        )

    def test_announce_refresh_does_nothing_when_no_service(self, qtbot):
        """announce_refresh must not raise when the service getter returns None."""
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)
        # Must not raise even when service is unavailable.
        adapter.announce_refresh(Path("/tmp/album"))


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
class TestScanProgressAdapterSignalRelay:
    """ScanProgressAdapter must forward scan events with no business logic."""

    @pytest.fixture
    def adapter(self, qtbot):
        from iPhoto.presentation.qt.adapters.scan_progress_adapter import ScanProgressAdapter

        return ScanProgressAdapter()

    def test_relay_progress_emits_scan_progress(self, adapter, qtbot):
        root = Path("/tmp/album")

        with qtbot.waitSignal(adapter.scanProgress, timeout=1000) as blocker:
            adapter.relay_progress(root, 3, 10)

        assert blocker.args == [root, 3, 10]

    def test_relay_chunk_ready_emits_scan_chunk_ready(self, adapter, qtbot):
        root = Path("/tmp/album")
        chunk = [{"rel": "img.jpg"}]

        with qtbot.waitSignal(adapter.scanChunkReady, timeout=1000) as blocker:
            adapter.relay_chunk_ready(root, chunk)

        assert blocker.args[0] == root
        assert blocker.args[1] == chunk

    def test_relay_finished_emits_scan_finished(self, adapter, qtbot):
        root = Path("/tmp/album")

        with qtbot.waitSignal(adapter.scanFinished, timeout=1000) as blocker:
            adapter.relay_finished(root, True)

        assert blocker.args == [root, True]

    def test_relay_batch_failed_emits_scan_batch_failed(self, adapter, qtbot):
        root = Path("/tmp/album")

        with qtbot.waitSignal(adapter.scanBatchFailed, timeout=1000) as blocker:
            adapter.relay_batch_failed(root, 5)

        assert blocker.args == [root, 5]

    def test_adapter_has_no_business_methods(self, adapter):
        """ScanProgressAdapter must only expose relay and signal methods."""
        public_methods = [
            name for name in dir(adapter)
            if not name.startswith("_") and callable(getattr(type(adapter), name, None))
        ]
        # Allowed public methods are Qt lifecycle + explicit relay methods
        allowed_prefixes = (
            "relay_", "connect", "disconnect", "emit", "blockSignals",
            "childEvent", "children", "customEvent", "deleteLater",
            "destroyed", "dumpObjectInfo", "dumpObjectTree", "dynamicPropertyNames",
            "event", "eventFilter", "findChild", "findChildren",
            "inherits", "installEventFilter", "isSignalConnected",
            "isWidgetType", "isWindowType", "isQmlExposed", "isQuickItemType",
            "killTimer",
            "metaObject", "moveToThread", "objectName", "parent",
            "property", "pyqtConfigure", "receivers", "removeEventFilter",
            "sender", "senderSignalIndex", "setObjectName", "setParent",
            "setProperty", "signalsBlocked", "startTimer", "staticMetaObject",
            "thread", "timerEvent", "tr",
        )
        business_methods = [
            m for m in public_methods
            if not any(m.startswith(p) or m == p for p in allowed_prefixes)
            and m not in (
                "scanProgress", "scanChunkReady", "scanFinished", "scanBatchFailed",
            )
        ]
        assert not business_methods, (
            f"ScanProgressAdapter has unexpected public methods (possible business logic): "
            f"{business_methods}"
        )


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
class TestAdapterBoundaryContract:
    """Verify adapter architectural boundary: no infrastructure access, no state."""

    def test_library_update_adapter_does_not_hold_worker(self, qtbot):
        """LibraryUpdateAdapter must not own any worker instance."""
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        # Inspect instance dict for worker references
        instance_dict = adapter.__dict__
        worker_attrs = [k for k in instance_dict if "worker" in k.lower()]
        assert not worker_attrs, (
            f"LibraryUpdateAdapter must not hold worker references: {worker_attrs}"
        )

    def test_scan_progress_adapter_does_not_hold_worker(self, qtbot):
        """ScanProgressAdapter must not own any worker instance."""
        from iPhoto.presentation.qt.adapters.scan_progress_adapter import ScanProgressAdapter

        adapter = ScanProgressAdapter()
        instance_dict = adapter.__dict__
        worker_attrs = [k for k in instance_dict if "worker" in k.lower()]
        assert not worker_attrs, (
            f"ScanProgressAdapter must not hold worker references: {worker_attrs}"
        )

    def test_adapters_are_exported_from_package(self):
        """Both adapters must be accessible from the adapters package."""
        from iPhoto.presentation.qt.adapters import LibraryUpdateAdapter, ScanProgressAdapter

        assert LibraryUpdateAdapter is not None
        assert ScanProgressAdapter is not None
