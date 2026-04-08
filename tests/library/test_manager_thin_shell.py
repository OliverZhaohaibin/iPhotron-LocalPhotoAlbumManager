"""Phase 4 tests: LibraryManager thin-shell behavior.

These tests verify that LibraryManager acts as a thin composition shell:
- It holds QObject signals and delegates to mixin/service logic.
- It does not contain standalone business rules.
- Its public API surface is stable across refactoring passes.
- Mixin classes contain the extracted logic (not the manager itself).
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
class TestLibraryManagerIsAShell:
    """LibraryManager must be a thin coordination shell, not a business logic owner."""

    @pytest.fixture
    def manager(self, qapp):
        from iPhoto.library.manager import LibraryManager

        return LibraryManager()

    def test_manager_has_signals(self, manager):
        """Manager must expose the expected Qt signals."""
        from iPhoto.library.manager import LibraryManager

        assert hasattr(LibraryManager, "treeUpdated")
        assert hasattr(LibraryManager, "errorRaised")
        assert hasattr(LibraryManager, "scanProgress")
        assert hasattr(LibraryManager, "scanChunkReady")
        assert hasattr(LibraryManager, "scanFinished")

    def test_manager_holds_application_services(self, manager):
        """Manager must delegate to application-layer services, not own the logic."""
        from iPhoto.application.services.library_tree_service import LibraryTreeService
        from iPhoto.application.services.library_scan_service import LibraryScanService
        from iPhoto.application.services.library_watch_service import LibraryWatchService
        from iPhoto.application.services.trash_service import TrashService

        assert isinstance(manager._tree_service, LibraryTreeService)
        assert isinstance(manager._scan_service, LibraryScanService)
        assert isinstance(manager._watch_service, LibraryWatchService)
        assert isinstance(manager._trash_service, TrashService)

    def test_manager_delegates_to_mixins(self, manager):
        """LibraryManager must inherit from all expected mixin classes."""
        from iPhoto.library.album_operations import AlbumOperationsMixin
        from iPhoto.library.filesystem_watcher import FileSystemWatcherMixin
        from iPhoto.library.geo_aggregator import GeoAggregatorMixin
        from iPhoto.library.scan_coordinator import ScanCoordinatorMixin
        from iPhoto.library.trash_manager import TrashManagerMixin

        assert isinstance(manager, AlbumOperationsMixin)
        assert isinstance(manager, ScanCoordinatorMixin)
        assert isinstance(manager, FileSystemWatcherMixin)
        assert isinstance(manager, GeoAggregatorMixin)
        assert isinstance(manager, TrashManagerMixin)

    def test_manager_root_initially_none(self, manager):
        """Manager root must be None before binding."""
        assert manager.root() is None

    def test_manager_list_albums_returns_list(self, manager):
        """list_albums must return a list even without a bound path."""
        result = manager.list_albums()
        assert isinstance(result, list)

    def test_manager_list_children_returns_list(self, manager):
        """list_children must return a list for any node."""
        from iPhoto.library.tree import AlbumNode

        node = AlbumNode(path=Path("/tmp/fake"), level=1, title="Fake", has_manifest=False)
        result = manager.list_children(node)
        assert isinstance(result, list)

    def test_manager_bind_path_raises_on_missing_dir(self, manager, tmp_path):
        """bind_path must raise LibraryUnavailableError for non-existent paths."""
        from iPhoto.errors import LibraryUnavailableError

        missing = tmp_path / "does_not_exist"
        with pytest.raises(LibraryUnavailableError):
            manager.bind_path(missing)

    def test_manager_bind_path_emits_tree_updated(self, manager, tmp_path, qapp):
        """bind_path must emit treeUpdated after successfully binding."""
        from PySide6.QtTest import QSignalSpy

        spy = QSignalSpy(manager.treeUpdated)
        manager.bind_path(tmp_path)
        assert spy.count() >= 1

    def test_manager_bind_path_sets_root(self, manager, tmp_path):
        """bind_path must set the internal root to the resolved path."""
        manager.bind_path(tmp_path)
        assert manager.root() == tmp_path.resolve()

    def test_manager_shutdown_does_not_raise(self, manager):
        """shutdown() must be safe to call even without a bound path."""
        manager.shutdown()  # Should not raise

    def test_manager_stop_scanning_does_not_raise(self, manager):
        """stop_scanning() must be safe to call without an active scan."""
        manager.stop_scanning()  # Should not raise

    def test_manager_no_business_logic_in_core_methods(self, manager, tmp_path):
        """bind_path must delegate tree-building to LibraryTreeService."""
        manager._tree_service = MagicMock()
        manager._tree_service.build_tree.return_value = ([], {}, {})
        manager._tree_service.iter_album_dirs.return_value = iter([])

        # bind_path internally calls _refresh_tree which calls _tree_service.build_tree
        manager.bind_path(tmp_path)
        manager._tree_service.build_tree.assert_called()


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
class TestLibraryManagerPublicApiStability:
    """Verify LibraryManager public API surface hasn't regressed."""

    def test_public_api_methods_exist(self):
        """All expected public methods must be present."""
        from iPhoto.library.manager import LibraryManager

        expected_methods = [
            "root",
            "bind_path",
            "list_albums",
            "list_children",
            "scan_tree",
            "shutdown",
            "start_scanning",
            "stop_scanning",
            "is_scanning_path",
            "get_live_scan_results",
            "create_album",
            "create_subalbum",
            "rename_album",
            "ensure_manifest",
            "deleted_directory",
            "ensure_deleted_directory",
            "cleanup_deleted_index",
            "pause_watcher",
            "resume_watcher",
        ]
        for method in expected_methods:
            assert hasattr(LibraryManager, method), (
                f"LibraryManager is missing expected public method: {method}"
            )

    def test_geo_tagged_asset_still_re_exported(self):
        """GeotaggedAsset must remain importable from library.manager for compatibility."""
        from iPhoto.library.manager import GeotaggedAsset  # noqa: F401 – compatibility re-export

        assert GeotaggedAsset is not None


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
class TestLibraryManagerMixinDelegation:
    """Verify mixin methods delegate to application services."""

    @pytest.fixture
    def manager_with_root(self, tmp_path, qapp):
        from iPhoto.library.manager import LibraryManager

        mgr = LibraryManager()
        mgr.bind_path(tmp_path)
        return mgr

    def test_scan_coordinator_delegates_to_scan_service(self, manager_with_root):
        """start_scanning must consult LibraryScanService.should_skip_start."""
        manager = manager_with_root
        original_service = manager._scan_service
        mock_service = MagicMock()
        mock_service.should_skip_start.return_value = True  # skip the actual scan
        manager._scan_service = mock_service

        manager.start_scanning(manager.root(), include=["*.jpg"], exclude=[])
        mock_service.should_skip_start.assert_called_once()

        manager._scan_service = original_service

    def test_deleted_directory_delegates_to_trash_manager_mixin(self, manager_with_root, tmp_path):
        """deleted_directory must go through TrashManagerMixin logic."""
        result = manager_with_root.deleted_directory()
        # Result may be None or a Path, but it must not raise.
        assert result is None or isinstance(result, Path)
