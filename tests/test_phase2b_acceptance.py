"""Phase 2 acceptance fix tests.

Covers the issues identified in evaluate step2.md:
- LibraryManager delegation behaviour (not just "has service instances")
- MoveBookkeepingService (stale tracking, album root location, restore targets)
- FileSystemWatcherMixin truly delegates to LibraryWatchService + QtLibraryWatcher
- TrashManagerMixin truly delegates to TrashService
- Restore chain integration (metadata preserved)
- Nested album with global db (prefix/strip/scope complete coverage)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch
import pytest

# ---------------------------------------------------------------------------
# MoveBookkeepingService
# ---------------------------------------------------------------------------
from iPhoto.application.services.move_bookkeeping_service import MoveBookkeepingService


class TestMoveBookkeepingServiceStaleTracking:
    def test_consume_returns_false_when_not_stale(self, tmp_path):
        svc = MoveBookkeepingService()
        assert svc.consume_forced_reload(tmp_path / "album") is False

    def test_consume_returns_true_after_mark(self, tmp_path):
        svc = MoveBookkeepingService()
        p = tmp_path / "album"
        svc.mark_stale(p)
        assert svc.consume_forced_reload(p) is True

    def test_consume_clears_marker(self, tmp_path):
        svc = MoveBookkeepingService()
        p = tmp_path / "album"
        svc.mark_stale(p)
        svc.consume_forced_reload(p)
        assert svc.consume_forced_reload(p) is False

    def test_reset_clears_all_stale_markers(self, tmp_path):
        svc = MoveBookkeepingService()
        svc.mark_stale(tmp_path / "a")
        svc.mark_stale(tmp_path / "b")
        svc.reset()
        assert svc.consume_forced_reload(tmp_path / "a") is False
        assert svc.consume_forced_reload(tmp_path / "b") is False

    def test_mark_same_path_twice_idempotent(self, tmp_path):
        svc = MoveBookkeepingService()
        p = tmp_path / "album"
        svc.mark_stale(p)
        svc.mark_stale(p)
        assert svc.consume_forced_reload(p) is True
        # Second consume is False — only one marker was stored
        assert svc.consume_forced_reload(p) is False


class TestMoveBookkeepingServiceAlbumRootLocation:
    def test_locate_album_root_with_work_dir(self, tmp_path):
        from iPhoto.config import WORK_DIR_NAME

        lib = tmp_path / "lib"
        album = lib / "Album"
        work = album / WORK_DIR_NAME
        work.mkdir(parents=True)

        svc = MoveBookkeepingService()
        result = svc.locate_album_root(album / "subdir", lib)
        assert result is not None
        assert result.resolve() == album.resolve()

    def test_locate_album_root_not_found(self, tmp_path):
        lib = tmp_path / "lib"
        lib.mkdir()
        svc = MoveBookkeepingService()
        result = svc.locate_album_root(lib / "subdir", lib)
        assert result is None

    def test_locate_album_root_caches_result(self, tmp_path):
        from iPhoto.config import WORK_DIR_NAME

        lib = tmp_path / "lib"
        album = lib / "Album"
        (album / WORK_DIR_NAME).mkdir(parents=True)

        svc = MoveBookkeepingService()
        r1 = svc.locate_album_root(album / "photo.jpg", lib)
        r2 = svc.locate_album_root(album / "photo.jpg", lib)
        assert r1 == r2

    def test_reset_clears_cache(self, tmp_path):
        from iPhoto.config import WORK_DIR_NAME

        lib = tmp_path / "lib"
        album = lib / "Album"
        (album / WORK_DIR_NAME).mkdir(parents=True)

        svc = MoveBookkeepingService()
        svc.locate_album_root(album, lib)
        assert len(svc._album_root_cache) > 0
        svc.reset()
        assert len(svc._album_root_cache) == 0


class TestMoveBookkeepingServiceCollectAlbumRoots:
    def test_returns_empty_on_empty_pairs(self, tmp_path):
        lib = tmp_path / "lib"
        lib.mkdir()
        svc = MoveBookkeepingService()
        assert svc.collect_album_roots_from_pairs([], lib) == set()

    def test_returns_album_roots_for_pairs(self, tmp_path):
        from iPhoto.config import WORK_DIR_NAME

        lib = tmp_path / "lib"
        album = lib / "Album"
        (album / WORK_DIR_NAME).mkdir(parents=True)
        photo = album / "photo.jpg"
        photo.touch()
        dest = album / "photo_copy.jpg"
        dest.touch()

        svc = MoveBookkeepingService()
        roots = svc.collect_album_roots_from_pairs([(photo, dest)], lib)
        assert album.resolve() in {r.resolve() for r in roots}


class TestMoveBookkeepingServiceRestoreTargets:
    def test_computes_restore_targets(self, tmp_path):
        lib = tmp_path / "lib"
        album = lib / "Album"
        album.mkdir(parents=True)
        trash = lib / ".deleted"
        trash.mkdir()

        # Restore from trash → album
        photo_in_trash = trash / "photo.jpg"
        photo_in_album = album / "photo.jpg"

        svc = MoveBookkeepingService()
        targets = svc.compute_restore_rescan_targets(
            [(photo_in_trash, photo_in_album)], lib
        )
        assert len(targets) == 1
        assert targets[0].resolve() == album.resolve()

    def test_filters_out_outside_library(self, tmp_path):
        lib = tmp_path / "lib"
        lib.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        trash = lib / ".deleted"
        trash.mkdir()

        svc = MoveBookkeepingService()
        targets = svc.compute_restore_rescan_targets(
            [(trash / "a.jpg", outside / "a.jpg")], lib
        )
        assert targets == []

    def test_deduplicates_same_album_root(self, tmp_path):
        lib = tmp_path / "lib"
        album = lib / "Album"
        album.mkdir(parents=True)

        svc = MoveBookkeepingService()
        pairs = [
            (lib / ".deleted" / "a.jpg", album / "a.jpg"),
            (lib / ".deleted" / "b.jpg", album / "b.jpg"),
        ]
        targets = svc.compute_restore_rescan_targets(pairs, lib)
        assert len(targets) == 1

    def test_no_library_root_accepts_all(self, tmp_path):
        album = tmp_path / "Album"
        album.mkdir()
        photo = album / "photo.jpg"

        svc = MoveBookkeepingService()
        targets = svc.compute_restore_rescan_targets(
            [(tmp_path / ".deleted" / "p.jpg", photo)], None
        )
        assert len(targets) == 1


# ---------------------------------------------------------------------------
# LibraryUpdateService – delegates to MoveBookkeepingService
# ---------------------------------------------------------------------------

class TestLibraryUpdateServiceDelegatesBookkeeping:
    """Verify that LibraryUpdateService delegates to MoveBookkeepingService."""

    def _make_service(self):
        """Return a wired LibraryUpdateService with mocked dependencies."""
        LibraryUpdateService = pytest.importorskip(
            "iPhoto.gui.services.library_update_service",
            reason="Qt not available",
        ).LibraryUpdateService
        BackgroundTaskManager = pytest.importorskip(
            "iPhoto.gui.background_task_manager",
            reason="Qt not available",
        ).BackgroundTaskManager

        task_manager = MagicMock(spec=BackgroundTaskManager)
        svc = LibraryUpdateService(
            task_manager=task_manager,
            current_album_getter=lambda: None,
            library_manager_getter=lambda: None,
        )
        return svc

    def test_consume_forced_reload_delegates(self, tmp_path):
        svc = self._make_service()
        # Nothing stale yet
        assert svc.consume_forced_reload(tmp_path) is False

        # Mark stale through the bookkeeping service directly
        svc._move_bookkeeping.mark_stale(tmp_path)
        assert svc.consume_forced_reload(tmp_path) is True
        assert svc.consume_forced_reload(tmp_path) is False

    def test_reset_cache_delegates_to_bookkeeping(self, tmp_path):
        svc = self._make_service()
        svc._move_bookkeeping.mark_stale(tmp_path)
        svc.reset_cache()
        assert svc.consume_forced_reload(tmp_path) is False

    def test_has_move_bookkeeping_service(self):
        from iPhoto.application.services.move_bookkeeping_service import MoveBookkeepingService

        svc = self._make_service()
        assert isinstance(svc._move_bookkeeping, MoveBookkeepingService)

    def test_no_stale_state_on_service_directly(self):
        """Verify that _stale_album_roots and _album_root_cache are gone from the service."""
        svc = self._make_service()
        assert not hasattr(svc, "_stale_album_roots"), (
            "_stale_album_roots should have been moved to MoveBookkeepingService"
        )
        assert not hasattr(svc, "_album_root_cache"), (
            "_album_root_cache should have been moved to MoveBookkeepingService"
        )


# ---------------------------------------------------------------------------
# FileSystemWatcherMixin – truly delegates to LibraryWatchService
# ---------------------------------------------------------------------------

class TestFileSystemWatcherMixinDelegation:
    """Verify that FileSystemWatcherMixin methods delegate to services, not local state."""

    def _make_fake_manager(self):
        """Build a minimal object combining the mixin with mock services."""
        from iPhoto.library.filesystem_watcher import FileSystemWatcherMixin
        from iPhoto.application.services.library_watch_service import LibraryWatchService
        from iPhoto.infrastructure.watcher.qt_library_watcher import QtLibraryWatcher

        class FakeManager(FileSystemWatcherMixin):
            def __init__(self):
                self._root = None
                self._albums = []
                self._watch_service = LibraryWatchService()
                qt_watcher = MagicMock(spec=QtLibraryWatcher)
                qt_watcher.sync_paths = MagicMock()
                self._qt_watcher = qt_watcher
                # Fake debounce timer
                self._debounce = MagicMock()
                self._debounce.isActive.return_value = False

        return FakeManager()

    def test_pause_watcher_delegates_to_watch_service(self):
        mgr = self._make_fake_manager()
        assert mgr._watch_service.suspend_depth() == 0
        mgr.pause_watcher()
        assert mgr._watch_service.suspend_depth() == 1

    def test_resume_watcher_delegates_to_watch_service(self):
        mgr = self._make_fake_manager()
        mgr.pause_watcher()
        mgr.resume_watcher()
        assert mgr._watch_service.suspend_depth() == 0

    def test_on_directory_changed_skipped_when_suspended(self):
        mgr = self._make_fake_manager()
        mgr.pause_watcher()
        mgr._on_directory_changed("/some/path")
        mgr._debounce.start.assert_not_called()

    def test_on_directory_changed_starts_debounce_when_active(self):
        mgr = self._make_fake_manager()
        mgr._on_directory_changed("/some/path")
        mgr._debounce.start.assert_called_once()

    def test_rebuild_watches_uses_qt_watcher(self, tmp_path):
        mgr = self._make_fake_manager()
        from unittest.mock import MagicMock
        from iPhoto.library.tree import AlbumNode

        mgr._root = tmp_path
        album_path = tmp_path / "Album"
        album_path.mkdir()
        node = AlbumNode(album_path, 0, "Album", False)
        mgr._albums = [node]

        mgr._rebuild_watches()

        mgr._qt_watcher.sync_paths.assert_called_once()
        desired_arg = mgr._qt_watcher.sync_paths.call_args[0][0]
        assert str(tmp_path) in desired_arg
        assert str(album_path) in desired_arg

    def test_no_watch_suspend_depth_on_manager(self):
        """Verify the removed _watch_suspend_depth field is gone from manager."""
        from iPhoto.library.manager import LibraryManager

        # We can inspect __init__ source to check the field is no longer there
        import inspect
        source = inspect.getsource(LibraryManager.__init__)
        assert "_watch_suspend_depth" not in source, (
            "_watch_suspend_depth should have been removed from LibraryManager.__init__"
        )


# ---------------------------------------------------------------------------
# TrashManagerMixin – delegates to TrashService
# ---------------------------------------------------------------------------

class TestTrashManagerMixinDelegation:
    def _make_fake_manager(self, tmp_path):
        from iPhoto.library.trash_manager import TrashManagerMixin
        from iPhoto.application.services.trash_service import TrashService

        class FakeManager(TrashManagerMixin):
            def __init__(self):
                self._root = tmp_path
                self._deleted_dir = None
                self._trash_service = TrashService()

            def _require_root(self):
                return self._root

            def errorRaised(self):
                pass

        mgr = FakeManager()
        mgr.errorRaised = MagicMock()
        return mgr

    def test_relative_deleted_album_path_delegates(self, tmp_path):
        lib = tmp_path / "lib"
        lib.mkdir()
        trash = lib / ".deleted"
        trash.mkdir()

        mgr = self._make_fake_manager(lib)
        result = mgr._relative_deleted_album_path(trash, lib)
        assert result == ".deleted"

    def test_relative_deleted_album_path_outside_returns_none(self, tmp_path):
        lib = tmp_path / "lib"
        lib.mkdir()
        outside = tmp_path / "other"
        outside.mkdir()

        mgr = self._make_fake_manager(lib)
        result = mgr._relative_deleted_album_path(outside, lib)
        assert result is None

    def test_trash_service_instance_present(self, tmp_path):
        from iPhoto.application.services.trash_service import TrashService
        mgr = self._make_fake_manager(tmp_path)
        assert isinstance(mgr._trash_service, TrashService)


# ---------------------------------------------------------------------------
# Restore chain integration
# ---------------------------------------------------------------------------

class TestRestoreChainIntegration:
    """Test that the restore chain preserves metadata end-to-end."""

    def test_merge_trash_metadata_preserves_original_rel_path(self, tmp_path):
        from iPhoto.config import RECENTLY_DELETED_DIR_NAME
        from iPhoto.application.use_cases.scan.merge_trash_restore_metadata_use_case import (
            MergeTrashRestoreMetadataUseCase,
        )

        trash_root = tmp_path / RECENTLY_DELETED_DIR_NAME
        trash_root.mkdir()

        mock_store = MagicMock()
        mock_store.read_all.return_value = [
            {
                "rel": "photo.jpg",
                "original_rel_path": "Portraits/photo.jpg",
                "original_album_id": "uuid-abc",
                "original_album_subpath": "Portraits",
            }
        ]

        with patch("iPhoto.cache.index_store.get_global_repository", return_value=mock_store):
            uc = MergeTrashRestoreMetadataUseCase()
            rows = [{"rel": "photo.jpg", "size": 5000}]
            result = uc.execute(rows, trash_root, library_root=None)

        assert len(result) == 1
        r = result[0]
        assert r["original_rel_path"] == "Portraits/photo.jpg"
        assert r["original_album_id"] == "uuid-abc"
        assert r["original_album_subpath"] == "Portraits"
        # Original fields preserved
        assert r["size"] == 5000

    def test_merge_trash_metadata_does_not_overwrite_existing(self, tmp_path):
        from iPhoto.config import RECENTLY_DELETED_DIR_NAME
        from iPhoto.application.use_cases.scan.merge_trash_restore_metadata_use_case import (
            MergeTrashRestoreMetadataUseCase,
        )

        trash_root = tmp_path / RECENTLY_DELETED_DIR_NAME
        trash_root.mkdir()

        mock_store = MagicMock()
        mock_store.read_all.return_value = [
            {"rel": "photo.jpg", "original_rel_path": "old_path/photo.jpg"}
        ]

        with patch("iPhoto.cache.index_store.get_global_repository", return_value=mock_store):
            uc = MergeTrashRestoreMetadataUseCase()
            rows = [{"rel": "photo.jpg", "original_rel_path": "new_path/photo.jpg"}]
            result = uc.execute(rows, trash_root, library_root=None)

        # existing value wins
        assert result[0]["original_rel_path"] == "new_path/photo.jpg"

    def test_non_trash_album_rows_unchanged(self, tmp_path):
        from iPhoto.application.use_cases.scan.merge_trash_restore_metadata_use_case import (
            MergeTrashRestoreMetadataUseCase,
        )

        regular_album = tmp_path / "Portraits"
        regular_album.mkdir()

        uc = MergeTrashRestoreMetadataUseCase()
        rows = [{"rel": "photo.jpg"}]
        result = uc.execute(rows, regular_album)
        assert result == rows

    def test_restore_rescan_targets_only_in_library(self, tmp_path):
        """Compute restore targets – only paths inside library_root survive."""
        lib = tmp_path / "lib"
        album = lib / "Album"
        album.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()

        svc = MoveBookkeepingService()
        pairs = [
            (lib / ".deleted" / "a.jpg", album / "a.jpg"),       # inside
            (lib / ".deleted" / "b.jpg", outside / "b.jpg"),     # outside
        ]
        targets = svc.compute_restore_rescan_targets(pairs, lib)
        target_norms = {t.resolve() for t in targets}
        assert album.resolve() in target_norms
        assert outside.resolve() not in target_norms


# ---------------------------------------------------------------------------
# Nested album + global db prefix/strip coverage
# ---------------------------------------------------------------------------

class TestNestedAlbumGlobalDb:
    """Verify AlbumPathPolicy handles deeply nested albums and global db."""

    def test_prefix_nested_rel(self):
        from iPhoto.application.policies.album_path_policy import AlbumPathPolicy

        policy = AlbumPathPolicy()
        rows = [{"rel": "photo.jpg"}, {"rel": "sub/shot.jpg"}]
        result = policy.prefix_rows(rows, "Parent/Child")
        rels = [r["rel"] for r in result]
        assert "Parent/Child/photo.jpg" in rels
        assert "Parent/Child/sub/shot.jpg" in rels

    def test_strip_nested_prefix(self):
        from iPhoto.application.policies.album_path_policy import AlbumPathPolicy

        policy = AlbumPathPolicy()
        rows = [
            {"rel": "Parent/Child/photo.jpg"},
            {"rel": "Parent/Child/sub/shot.jpg"},
            {"rel": "Parent/Other/img.jpg"},  # different album → dropped
        ]
        result = policy.strip_album_prefix(rows, "Parent/Child")
        rels = [r["rel"] for r in result]
        assert "photo.jpg" in rels
        assert "sub/shot.jpg" in rels
        # Rows from sibling album are excluded
        assert not any("Parent/Other" in r["rel"] for r in result)

    def test_compute_album_path_deeply_nested(self, tmp_path):
        from iPhoto.application.policies.album_path_policy import AlbumPathPolicy

        lib = tmp_path / "lib"
        deep = lib / "A" / "B" / "C"
        deep.mkdir(parents=True)

        policy = AlbumPathPolicy()
        result = policy.compute_album_path(deep, lib)
        assert result == "A/B/C"

    def test_is_within_scope_nested_include(self, tmp_path):
        from iPhoto.application.policies.album_path_policy import AlbumPathPolicy

        policy = AlbumPathPolicy()
        album_root = tmp_path / "album"
        nested = album_root / "sub" / "deep" / "photo.jpg"
        nested.parent.mkdir(parents=True)
        nested.touch()

        assert policy.is_within_scope(nested, album_root, include_subalbums=True) is True
        assert policy.is_within_scope(nested, album_root, include_subalbums=False) is False

    def test_library_scope_nested_album_membership(self, tmp_path):
        from iPhoto.application.policies.library_scope_policy import LibraryScopePolicy

        lib = tmp_path / "lib"
        nested = lib / "A" / "B"
        nested.mkdir(parents=True)

        policy = LibraryScopePolicy()
        assert policy.is_within_library(nested, lib) is True
        assert policy.library_relative_path(nested, lib) == "A/B"

    def test_library_scope_outside_library(self, tmp_path):
        from iPhoto.application.policies.library_scope_policy import LibraryScopePolicy

        lib = tmp_path / "lib"
        lib.mkdir()
        outside = tmp_path / "other"
        outside.mkdir()

        policy = LibraryScopePolicy()
        assert policy.is_within_library(outside, lib) is False
        assert policy.library_relative_path(outside, lib) is None

    def test_cross_library_move_detection(self, tmp_path):
        from iPhoto.application.policies.library_scope_policy import LibraryScopePolicy

        lib = tmp_path / "lib"
        inside = lib / "album"
        inside.mkdir(parents=True)
        outside = tmp_path / "other"
        outside.mkdir()

        policy = LibraryScopePolicy()
        assert policy.is_cross_library_move(inside, outside, lib) is True
        assert policy.is_cross_library_move(inside, inside / "sub", lib) is False


# ---------------------------------------------------------------------------
# LibraryManager delegation – watcher service really used
# ---------------------------------------------------------------------------

class TestLibraryManagerWatcherDelegation:
    """Verify manager.py wires FileSystemWatcherMixin to the Phase 2 services."""

    def test_manager_imports_service_classes(self):
        """All Phase 2 service classes must be importable from manager module."""
        import iPhoto.library.manager as m

        assert hasattr(m, "LibraryTreeService")
        assert hasattr(m, "LibraryScanService")
        assert hasattr(m, "LibraryWatchService")
        assert hasattr(m, "TrashService")
        assert hasattr(m, "QtLibraryWatcher")

    def test_watcher_mixin_no_longer_uses_watch_suspend_depth(self):
        """FileSystemWatcherMixin source must not reference _watch_suspend_depth."""
        import inspect
        from iPhoto.library.filesystem_watcher import FileSystemWatcherMixin

        source = inspect.getsource(FileSystemWatcherMixin)
        assert "_watch_suspend_depth" not in source, (
            "FileSystemWatcherMixin should delegate to _watch_service, not track depth itself"
        )

    def test_filesystem_watcher_mixin_uses_qt_watcher(self):
        """_rebuild_watches must call self._qt_watcher, not self._watcher directly."""
        import inspect
        from iPhoto.library.filesystem_watcher import FileSystemWatcherMixin

        source = inspect.getsource(FileSystemWatcherMixin._rebuild_watches)
        assert "_qt_watcher" in source
        assert "self._watcher" not in source

    def test_trash_mixin_delegates_to_service(self):
        """_relative_deleted_album_path must delegate to self._trash_service."""
        import inspect
        from iPhoto.library.trash_manager import TrashManagerMixin

        source = inspect.getsource(TrashManagerMixin._relative_deleted_album_path)
        assert "_trash_service" in source

    def test_filesystem_watcher_pause_uses_watch_service(self):
        """pause_watcher must call self._watch_service.pause()."""
        import inspect
        from iPhoto.library.filesystem_watcher import FileSystemWatcherMixin

        source = inspect.getsource(FileSystemWatcherMixin.pause_watcher)
        assert "_watch_service.pause" in source

    def test_filesystem_watcher_resume_uses_watch_service(self):
        """resume_watcher must call self._watch_service.resume()."""
        import inspect
        from iPhoto.library.filesystem_watcher import FileSystemWatcherMixin

        source = inspect.getsource(FileSystemWatcherMixin.resume_watcher)
        assert "_watch_service.resume" in source
