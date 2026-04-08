"""Phase 4 tests: LibraryUpdateAdapter and ScanProgressAdapter boundary.

These tests verify that the Qt adapters in
``src/iPhoto/presentation/qt/adapters/`` act as pure signal-relay objects:
- They forward signals from the service layer to UI subscribers.
- They contain no business rules.
- They do not access infrastructure directly.
- They do not hold worker lifecycle state.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_pyside6_available = False
try:
    import PySide6  # noqa: F401

    _pyside6_available = True
except ImportError:
    pass


@pytest.fixture(scope="module")
def qapp():
    if not _pyside6_available:
        pytest.skip("PySide6 not installed")
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
class TestLibraryUpdateAdapterSignalRelay:
    """LibraryUpdateAdapter must forward service signals with no transformation."""

    def test_index_updated_is_relayed(self, qapp):
        from PySide6.QtTest import QSignalSpy

        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        root = Path("/tmp/album")
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        spy = QSignalSpy(adapter.indexUpdated)
        adapter._on_index_updated(root)

        assert spy.count() == 1
        assert spy[0][0] == root

    def test_links_updated_is_relayed(self, qapp):
        from PySide6.QtTest import QSignalSpy

        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        root = Path("/tmp/album")
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        spy = QSignalSpy(adapter.linksUpdated)
        adapter._on_links_updated(root)

        assert spy.count() == 1
        assert spy[0][0] == root

    def test_asset_reload_requested_is_relayed(self, qapp):
        from PySide6.QtTest import QSignalSpy

        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        root = Path("/tmp/album")
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        spy = QSignalSpy(adapter.assetReloadRequested)
        adapter._on_asset_reload_requested(root, False, True)

        assert spy.count() == 1
        assert spy[0][0] == root
        assert spy[0][1] is False
        assert spy[0][2] is True

    def test_error_raised_is_relayed(self, qapp):
        from PySide6.QtTest import QSignalSpy

        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        spy = QSignalSpy(adapter.errorRaised)
        adapter._on_error_raised("something went wrong")

        assert spy.count() == 1
        assert spy[0][0] == "something went wrong"

    def test_scan_progress_is_relayed(self, qapp):
        from PySide6.QtTest import QSignalSpy

        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        root = Path("/tmp/album")
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        spy = QSignalSpy(adapter.scanProgress)
        adapter._on_scan_progress(root, 5, 10)

        assert spy.count() == 1
        assert spy[0][0] == root
        assert spy[0][1] == 5
        assert spy[0][2] == 10

    def test_scan_chunk_ready_is_relayed(self, qapp):
        from PySide6.QtTest import QSignalSpy

        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        root = Path("/tmp/album")
        chunk = [{"rel": "photo.jpg"}]
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        spy = QSignalSpy(adapter.scanChunkReady)
        adapter._on_scan_chunk_ready(root, chunk)

        assert spy.count() == 1
        assert spy[0][0] == root
        assert spy[0][1] == chunk

    def test_scan_finished_is_relayed(self, qapp):
        from PySide6.QtTest import QSignalSpy

        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        root = Path("/tmp/album")
        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        spy = QSignalSpy(adapter.scanFinished)
        adapter._on_scan_finished(root, True)

        assert spy.count() == 1
        assert spy[0][0] == root
        assert spy[0][1] is True

    def test_wire_service_connects_all_signals(self, qapp):
        """wire_service must connect all expected service signals to relay slots."""
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtTest import QSignalSpy

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
        spy = QSignalSpy(adapter.indexUpdated)
        svc.indexUpdated.emit(root)

        assert spy.count() == 1
        assert spy[0][0] == root

    def test_announce_refresh_delegates_to_service(self, qapp):
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

    def test_announce_refresh_does_nothing_when_no_service(self, qapp):
        """announce_refresh must not raise when the service getter returns None."""
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)
        # Must not raise even when service is unavailable.
        adapter.announce_refresh(Path("/tmp/album"))


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
class TestScanProgressAdapterSignalRelay:
    """ScanProgressAdapter must forward scan events with no business logic."""

    @pytest.fixture
    def adapter(self, qapp):
        from iPhoto.presentation.qt.adapters.scan_progress_adapter import ScanProgressAdapter

        return ScanProgressAdapter()

    def test_relay_progress_emits_scan_progress(self, adapter, qapp):
        from PySide6.QtTest import QSignalSpy

        root = Path("/tmp/album")

        spy = QSignalSpy(adapter.scanProgress)
        adapter.relay_progress(root, 3, 10)

        assert spy.count() == 1
        assert spy[0][0] == root
        assert spy[0][1] == 3
        assert spy[0][2] == 10

    def test_relay_chunk_ready_emits_scan_chunk_ready(self, adapter, qapp):
        from PySide6.QtTest import QSignalSpy

        root = Path("/tmp/album")
        chunk = [{"rel": "img.jpg"}]

        spy = QSignalSpy(adapter.scanChunkReady)
        adapter.relay_chunk_ready(root, chunk)

        assert spy.count() == 1
        assert spy[0][0] == root
        assert spy[0][1] == chunk

    def test_relay_finished_emits_scan_finished(self, adapter, qapp):
        from PySide6.QtTest import QSignalSpy

        root = Path("/tmp/album")

        spy = QSignalSpy(adapter.scanFinished)
        adapter.relay_finished(root, True)

        assert spy.count() == 1
        assert spy[0][0] == root
        assert spy[0][1] is True

    def test_relay_batch_failed_emits_scan_batch_failed(self, adapter, qapp):
        from PySide6.QtTest import QSignalSpy

        root = Path("/tmp/album")

        spy = QSignalSpy(adapter.scanBatchFailed)
        adapter.relay_batch_failed(root, 5)

        assert spy.count() == 1
        assert spy[0][0] == root
        assert spy[0][1] == 5

    def test_adapter_has_no_business_methods(self, adapter):
        """ScanProgressAdapter must only expose relay and signal methods defined on the class."""
        from iPhoto.presentation.qt.adapters.scan_progress_adapter import ScanProgressAdapter

        # Only inspect methods/attributes defined directly on ScanProgressAdapter itself,
        # not inherited Qt/QObject members.  This avoids brittleness across PySide6 versions.
        own_public = {
            name for name in type(adapter).__dict__
            if not name.startswith("_")
        }
        # Signal descriptors and relay methods are the only allowed own members.
        # staticMetaObject is injected by the PySide6 metaclass into every QObject subclass.
        allowed_signals = {"scanProgress", "scanChunkReady", "scanFinished", "scanBatchFailed"}
        allowed_relay = {name for name in own_public if name.startswith("relay_")}
        qt_meta_attrs = {"staticMetaObject"}
        unexpected = own_public - allowed_signals - allowed_relay - qt_meta_attrs
        assert not unexpected, (
            f"ScanProgressAdapter has unexpected own public members (possible business logic): "
            f"{unexpected}"
        )


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
class TestAdapterBoundaryContract:
    """Verify adapter architectural boundary: no infrastructure access, no state."""

    def test_library_update_adapter_does_not_hold_worker(self, qapp):
        """LibraryUpdateAdapter must not own any worker instance."""
        from iPhoto.presentation.qt.adapters.library_update_adapter import LibraryUpdateAdapter

        adapter = LibraryUpdateAdapter(update_service_getter=lambda: None)

        # Inspect instance dict for worker references
        instance_dict = adapter.__dict__
        worker_attrs = [k for k in instance_dict if "worker" in k.lower()]
        assert not worker_attrs, (
            f"LibraryUpdateAdapter must not hold worker references: {worker_attrs}"
        )

    def test_scan_progress_adapter_does_not_hold_worker(self, qapp):
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
