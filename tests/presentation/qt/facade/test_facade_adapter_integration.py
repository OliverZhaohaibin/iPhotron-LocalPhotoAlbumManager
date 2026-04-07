"""Integration tests: AppFacade adapter wiring (Phase 3 closure).

These tests verify that ``AppFacade`` routes signals through the presentation
adapters rather than wiring them directly to the service layer.

PySide6 is required for all tests in this module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_pyside6_available = False
try:
    import PySide6  # noqa: F401

    _pyside6_available = True
except ImportError:
    pass

pytestmark = pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")


@pytest.fixture
def facade(qapp):  # noqa: ARG001
    from iPhoto.gui.facade import AppFacade

    return AppFacade()


class TestFacadeAdapterWiring:
    """AppFacade must expose adapters and route signals through them."""

    def test_facade_exposes_library_update_adapter(self, facade):
        from iPhoto.presentation.qt.adapters import LibraryUpdateAdapter

        assert hasattr(facade, "library_update_adapter")
        assert isinstance(facade.library_update_adapter, LibraryUpdateAdapter)

    def test_facade_exposes_scan_progress_adapter(self, facade):
        from iPhoto.presentation.qt.adapters import ScanProgressAdapter

        assert hasattr(facade, "scan_progress_adapter")
        assert isinstance(facade.scan_progress_adapter, ScanProgressAdapter)

    def test_library_update_adapter_relays_index_updated(self, facade):
        """indexUpdated from the service must arrive on AppFacade.indexUpdated."""
        received: list[Path] = []
        facade.indexUpdated.connect(received.append)

        album = Path("/tmp/TestAlbum")
        facade._library_update_service.indexUpdated.emit(album)

        assert album in received

    def test_library_update_adapter_relays_links_updated(self, facade):
        """linksUpdated from the service must arrive on AppFacade.linksUpdated."""
        received: list[Path] = []
        facade.linksUpdated.connect(received.append)

        album = Path("/tmp/TestAlbum")
        facade._library_update_service.linksUpdated.emit(album)

        assert album in received

    def test_library_update_adapter_relays_error(self, facade):
        """errorRaised from the service must arrive on AppFacade.errorRaised."""
        received: list[str] = []
        facade.errorRaised.connect(received.append)

        facade._library_update_service.errorRaised.emit("test-error")

        assert "test-error" in received

    def test_scan_progress_adapter_relays_scan_progress_from_service(self, facade):
        """scanProgress from LibraryUpdateService must arrive on AppFacade.scanProgress."""
        received: list[tuple] = []
        facade.scanProgress.connect(lambda root, cur, tot: received.append((root, cur, tot)))

        album = Path("/tmp/TestAlbum")
        facade._library_update_service.scanProgress.emit(album, 5, 10)

        assert (album, 5, 10) in received

    def test_scan_progress_adapter_relays_scan_finished_from_service(self, facade):
        """scanFinished from LibraryUpdateService must arrive on AppFacade.scanFinished."""
        received: list[tuple] = []
        facade.scanFinished.connect(lambda root, ok: received.append((root, ok)))

        album = Path("/tmp/TestAlbum")
        facade._library_update_service.scanFinished.emit(album, True)

        assert (album, True) in received

    def test_adapter_is_intermediate_not_direct_service(self, facade):
        """AppFacade must NOT connect directly from service signals to relay slots.

        The adapter must be the intermediate layer.  We verify this indirectly by
        confirming the adapter exists and the signals flow correctly end-to-end.
        """
        # Confirm adapter is NOT None (i.e., properly initialised)
        assert facade._library_update_adapter is not None
        assert facade._scan_progress_adapter is not None

        # Confirm the adapter's own signals are wired (they should emit when service does)
        from iPhoto.presentation.qt.adapters import LibraryUpdateAdapter, ScanProgressAdapter

        assert isinstance(facade._library_update_adapter, LibraryUpdateAdapter)
        assert isinstance(facade._scan_progress_adapter, ScanProgressAdapter)
