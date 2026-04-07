"""Phase 2 refactoring tests.

Covers the new policy layer, scan use cases, infrastructure adapters, and
application services introduced in the second refactoring phase.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# ---------------------------------------------------------------------------
# AlbumPathPolicy
# ---------------------------------------------------------------------------
from iPhoto.application.policies.album_path_policy import AlbumPathPolicy


class TestAlbumPathPolicy:
    def test_compute_album_path_returns_relative(self, tmp_path):
        library = tmp_path / "library"
        library.mkdir()
        album = library / "My Album"
        album.mkdir()
        policy = AlbumPathPolicy()
        assert policy.compute_album_path(album, library) == "My Album"

    def test_compute_album_path_nested(self, tmp_path):
        library = tmp_path / "library"
        sub = library / "Parent" / "Child"
        sub.mkdir(parents=True)
        policy = AlbumPathPolicy()
        assert policy.compute_album_path(sub, library) == "Parent/Child"

    def test_compute_album_path_outside_library_returns_none(self, tmp_path):
        library = tmp_path / "lib"
        library.mkdir()
        outside = tmp_path / "other"
        outside.mkdir()
        policy = AlbumPathPolicy()
        assert policy.compute_album_path(outside, library) is None

    def test_compute_album_path_no_library_root_returns_none(self, tmp_path):
        policy = AlbumPathPolicy()
        assert policy.compute_album_path(tmp_path, None) is None

    def test_compute_album_path_at_library_root_returns_none(self, tmp_path):
        policy = AlbumPathPolicy()
        assert policy.compute_album_path(tmp_path, tmp_path) is None

    def test_prefix_rows_adds_album_prefix(self):
        policy = AlbumPathPolicy()
        rows = [{"rel": "photo.jpg"}, {"rel": "video.mp4"}]
        result = policy.prefix_rows(rows, "Album")
        assert result[0]["rel"] == "Album/photo.jpg"
        assert result[1]["rel"] == "Album/video.mp4"

    def test_prefix_rows_skips_already_prefixed(self):
        policy = AlbumPathPolicy()
        rows = [{"rel": "Album/photo.jpg"}]
        result = policy.prefix_rows(rows, "Album")
        assert result[0]["rel"] == "Album/photo.jpg"

    def test_prefix_rows_empty_album_path_noop(self):
        policy = AlbumPathPolicy()
        rows = [{"rel": "photo.jpg"}]
        result = policy.prefix_rows(rows, "")
        assert result[0]["rel"] == "photo.jpg"

    def test_strip_album_prefix_removes_prefix(self):
        policy = AlbumPathPolicy()
        rows = [
            {"rel": "Album/photo.jpg"},
            {"rel": "Album/subdir/img.jpg"},
            {"rel": "orphan.jpg"},  # no prefix, no slash → kept
        ]
        result = policy.strip_album_prefix(rows, "Album")
        rels = [r["rel"] for r in result]
        assert "photo.jpg" in rels
        assert "orphan.jpg" in rels
        # Rows not matching album prefix are dropped
        assert not any(r["rel"].startswith("Album/") for r in result)

    def test_is_within_scope_direct_child(self, tmp_path):
        policy = AlbumPathPolicy()
        album_root = tmp_path / "album"
        album_root.mkdir()
        child = album_root / "photo.jpg"
        child.touch()
        assert policy.is_within_scope(child, album_root, include_subalbums=False) is True

    def test_is_within_scope_nested_excluded_by_default(self, tmp_path):
        policy = AlbumPathPolicy()
        album_root = tmp_path / "album"
        sub = album_root / "sub"
        sub.mkdir(parents=True)
        nested = sub / "img.jpg"
        nested.touch()
        assert policy.is_within_scope(nested, album_root, include_subalbums=False) is False

    def test_is_within_scope_nested_included_with_flag(self, tmp_path):
        policy = AlbumPathPolicy()
        album_root = tmp_path / "album"
        sub = album_root / "sub"
        sub.mkdir(parents=True)
        nested = sub / "img.jpg"
        nested.touch()
        assert policy.is_within_scope(nested, album_root, include_subalbums=True) is True


# ---------------------------------------------------------------------------
# LibraryScopePolicy
# ---------------------------------------------------------------------------
from iPhoto.application.policies.library_scope_policy import LibraryScopePolicy


class TestLibraryScopePolicy:
    def test_is_within_library_direct_child(self, tmp_path):
        policy = LibraryScopePolicy()
        lib = tmp_path / "lib"
        lib.mkdir()
        child = lib / "album"
        child.mkdir()
        assert policy.is_within_library(child, lib) is True

    def test_is_within_library_same_as_root(self, tmp_path):
        policy = LibraryScopePolicy()
        assert policy.is_within_library(tmp_path, tmp_path) is True

    def test_is_within_library_outside(self, tmp_path):
        policy = LibraryScopePolicy()
        other = tmp_path / "other"
        other.mkdir()
        lib = tmp_path / "lib"
        lib.mkdir()
        assert policy.is_within_library(other, lib) is False

    def test_is_cross_library_move_same_library(self, tmp_path):
        policy = LibraryScopePolicy()
        lib = tmp_path / "lib"
        lib.mkdir()
        src = lib / "a"
        src.mkdir()
        dst = lib / "b"
        dst.mkdir()
        assert policy.is_cross_library_move(src, dst, lib) is False

    def test_is_cross_library_move_different_libraries(self, tmp_path):
        policy = LibraryScopePolicy()
        lib = tmp_path / "lib"
        lib.mkdir()
        inside = lib / "a"
        inside.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        assert policy.is_cross_library_move(inside, outside, lib) is True

    def test_library_relative_path(self, tmp_path):
        policy = LibraryScopePolicy()
        lib = tmp_path / "lib"
        album = lib / "folder" / "sub"
        album.mkdir(parents=True)
        assert policy.library_relative_path(album, lib) == "folder/sub"

    def test_library_relative_path_outside_returns_none(self, tmp_path):
        policy = LibraryScopePolicy()
        lib = tmp_path / "lib"
        lib.mkdir()
        outside = tmp_path / "other"
        outside.mkdir()
        assert policy.library_relative_path(outside, lib) is None


# ---------------------------------------------------------------------------
# TrashRestorePolicy
# ---------------------------------------------------------------------------
from iPhoto.application.policies.trash_restore_policy import TrashRestorePolicy, PRESERVED_FIELDS


class TestTrashRestorePolicy:
    def test_merge_preserved_metadata_fills_missing_fields(self):
        policy = TrashRestorePolicy()
        new_rows = [{"rel": "photo.jpg"}]
        preserved = {
            "photo.jpg": {
                "rel": "photo.jpg",
                "original_rel_path": "album/photo.jpg",
                "original_album_id": "uuid-1",
            }
        }
        result = policy.merge_preserved_metadata(new_rows, preserved)
        assert result[0]["original_rel_path"] == "album/photo.jpg"
        assert result[0]["original_album_id"] == "uuid-1"

    def test_merge_preserved_metadata_does_not_overwrite_existing(self):
        policy = TrashRestorePolicy()
        new_rows = [{"rel": "photo.jpg", "original_rel_path": "existing_path"}]
        preserved = {
            "photo.jpg": {
                "rel": "photo.jpg",
                "original_rel_path": "old_path",
            }
        }
        result = policy.merge_preserved_metadata(new_rows, preserved)
        assert result[0]["original_rel_path"] == "existing_path"

    def test_merge_preserved_metadata_empty_preserved_noop(self):
        policy = TrashRestorePolicy()
        rows = [{"rel": "photo.jpg"}]
        result = policy.merge_preserved_metadata(rows, {})
        assert result == [{"rel": "photo.jpg"}]

    def test_resolve_trash_album_path_no_library(self, tmp_path):
        policy = TrashRestorePolicy()
        trash = tmp_path / ".deleted"
        album_path, allow_read_all = policy.resolve_trash_album_path(trash, None)
        assert album_path is None
        assert allow_read_all is True

    def test_resolve_trash_album_path_with_library(self, tmp_path):
        policy = TrashRestorePolicy()
        lib = tmp_path / "lib"
        lib.mkdir()
        trash = lib / ".deleted"
        trash.mkdir()
        album_path, allow_read_all = policy.resolve_trash_album_path(trash, lib)
        assert album_path == ".deleted"
        assert allow_read_all is False

    def test_resolve_trash_album_path_outside_library(self, tmp_path):
        policy = TrashRestorePolicy()
        lib = tmp_path / "lib"
        lib.mkdir()
        trash = tmp_path / "other" / ".deleted"
        trash.mkdir(parents=True)
        album_path, allow_read_all = policy.resolve_trash_album_path(trash, lib)
        # Path resolution failed, so album_path=None and allow_read_all=False
        assert album_path is None
        assert allow_read_all is False


# ---------------------------------------------------------------------------
# LoadIncrementalIndexUseCase
# ---------------------------------------------------------------------------
from iPhoto.application.use_cases.scan.load_incremental_index_use_case import (
    LoadIncrementalIndexUseCase,
)


class TestLoadIncrementalIndexUseCase:
    def test_returns_empty_dict_on_corrupted_index(self, tmp_path):
        from iPhoto.errors import IndexCorruptedError

        mock_store = MagicMock()
        mock_store.read_all.side_effect = IndexCorruptedError("corrupted")

        with patch(
            "iPhoto.cache.index_store.get_global_repository",
            return_value=mock_store,
        ):
            uc = LoadIncrementalIndexUseCase()
            result = uc.execute(tmp_path, library_root=None)
        assert result == {}

    def test_returns_rel_keyed_dict(self, tmp_path):
        mock_store = MagicMock()
        mock_store.read_all.return_value = [
            {"rel": "photo.jpg", "size": 100},
            {"rel": "video.mp4", "size": 200},
        ]

        with patch(
            "iPhoto.cache.index_store.get_global_repository",
            return_value=mock_store,
        ):
            uc = LoadIncrementalIndexUseCase()
            result = uc.execute(tmp_path, library_root=None)
        assert "photo.jpg" in result
        assert "video.mp4" in result


# ---------------------------------------------------------------------------
# MergeTrashRestoreMetadataUseCase
# ---------------------------------------------------------------------------
from iPhoto.application.use_cases.scan.merge_trash_restore_metadata_use_case import (
    MergeTrashRestoreMetadataUseCase,
)


class TestMergeTrashRestoreMetadataUseCase:
    def test_non_trash_album_returns_rows_unchanged(self, tmp_path):
        regular_album = tmp_path / "My Album"
        regular_album.mkdir()
        rows = [{"rel": "photo.jpg"}]
        uc = MergeTrashRestoreMetadataUseCase()
        result = uc.execute(rows, regular_album)
        assert result == rows

    def test_trash_album_merges_preserved_metadata(self, tmp_path):
        from iPhoto.config import RECENTLY_DELETED_DIR_NAME

        trash_root = tmp_path / RECENTLY_DELETED_DIR_NAME
        trash_root.mkdir()

        mock_store = MagicMock()
        mock_store.read_all.return_value = [
            {
                "rel": "photo.jpg",
                "original_rel_path": "old/photo.jpg",
                "original_album_id": "uuid-1",
            }
        ]

        with patch(
            "iPhoto.cache.index_store.get_global_repository",
            return_value=mock_store,
        ):
            uc = MergeTrashRestoreMetadataUseCase()
            rows = [{"rel": "photo.jpg"}]
            result = uc.execute(rows, trash_root, library_root=None)

        assert result[0].get("original_rel_path") == "old/photo.jpg"
        assert result[0].get("original_album_id") == "uuid-1"


# ---------------------------------------------------------------------------
# LibraryTreeService
# ---------------------------------------------------------------------------
from iPhoto.application.services.library_tree_service import LibraryTreeService


class TestLibraryTreeService:
    def test_build_tree_returns_sorted_albums(self, tmp_path):
        # Create album dirs
        (tmp_path / "Zebra").mkdir()
        (tmp_path / "Alpha").mkdir()
        (tmp_path / "Mango").mkdir()

        service = LibraryTreeService()
        # Use iter_album_dirs as the iter function; build_node builds AlbumNode
        from iPhoto.library.tree import AlbumNode

        def _build_node(path, level):
            return AlbumNode(path, level, path.name, False)

        albums, children, nodes = service.build_tree(
            tmp_path,
            lambda r: service.iter_album_dirs(r),
            _build_node,
        )
        titles = [a.title for a in albums]
        assert titles == sorted(titles, key=str.casefold)

    def test_iter_album_dirs_skips_work_dir(self, tmp_path):
        from iPhoto.config import WORK_DIR_NAME

        (tmp_path / "Album").mkdir()
        (tmp_path / WORK_DIR_NAME).mkdir()

        service = LibraryTreeService()
        dirs = list(service.iter_album_dirs(tmp_path))
        names = [d.name for d in dirs]
        assert WORK_DIR_NAME not in names
        assert "Album" in names

    def test_iter_album_dirs_skips_recently_deleted(self, tmp_path):
        from iPhoto.config import RECENTLY_DELETED_DIR_NAME

        (tmp_path / "Album").mkdir()
        (tmp_path / RECENTLY_DELETED_DIR_NAME).mkdir()

        service = LibraryTreeService()
        dirs = list(service.iter_album_dirs(tmp_path))
        names = [d.name for d in dirs]
        assert RECENTLY_DELETED_DIR_NAME not in names


# ---------------------------------------------------------------------------
# LibraryScanService
# ---------------------------------------------------------------------------
from iPhoto.application.services.library_scan_service import LibraryScanService


class TestLibraryScanService:
    def test_initial_state_not_scanning(self):
        svc = LibraryScanService()
        assert svc.is_scanning() is False
        assert svc.current_scan_root() is None

    def test_mark_started_sets_state(self, tmp_path):
        svc = LibraryScanService()
        svc.mark_started(tmp_path)
        assert svc.is_scanning() is True
        assert svc.current_scan_root() == tmp_path

    def test_mark_stopped_clears_state(self, tmp_path):
        svc = LibraryScanService()
        svc.mark_started(tmp_path)
        svc.mark_stopped()
        assert svc.is_scanning() is False
        assert svc.current_scan_root() is None

    def test_is_scanning_path_true_for_root(self, tmp_path):
        svc = LibraryScanService()
        svc.mark_started(tmp_path)
        assert svc.is_scanning_path(tmp_path) is True

    def test_is_scanning_path_false_when_stopped(self, tmp_path):
        svc = LibraryScanService()
        svc.mark_started(tmp_path)
        svc.mark_stopped()
        assert svc.is_scanning_path(tmp_path) is False


# ---------------------------------------------------------------------------
# LibraryWatchService
# ---------------------------------------------------------------------------
from iPhoto.application.services.library_watch_service import LibraryWatchService


class TestLibraryWatchService:
    def test_initial_not_suspended(self):
        svc = LibraryWatchService()
        assert svc.is_suspended() is False
        assert svc.suspend_depth() == 0

    def test_pause_increments_depth(self):
        svc = LibraryWatchService()
        svc.pause()
        assert svc.suspend_depth() == 1
        assert svc.is_suspended() is True

    def test_resume_decrements_depth(self):
        svc = LibraryWatchService()
        svc.pause()
        svc.resume()
        assert svc.suspend_depth() == 0
        assert svc.is_suspended() is False

    def test_resume_noop_when_not_suspended(self):
        svc = LibraryWatchService()
        svc.resume()  # Should not raise
        assert svc.suspend_depth() == 0

    def test_nested_pause_resume(self):
        svc = LibraryWatchService()
        svc.pause()
        svc.pause()
        assert svc.suspend_depth() == 2
        svc.resume()
        assert svc.suspend_depth() == 1
        assert svc.is_suspended() is True
        svc.resume()
        assert svc.is_suspended() is False

    def test_compute_desired_paths(self, tmp_path):
        svc = LibraryWatchService()
        lib = tmp_path / "lib"
        album = lib / "Album"
        desired = svc.compute_desired_paths(lib, [album])
        assert str(lib) in desired
        assert str(album) in desired

    def test_compute_desired_paths_no_library_root(self, tmp_path):
        svc = LibraryWatchService()
        album = tmp_path / "Album"
        desired = svc.compute_desired_paths(None, [album])
        assert str(album) in desired
        assert str(tmp_path) not in desired


# ---------------------------------------------------------------------------
# TrashService
# ---------------------------------------------------------------------------
from iPhoto.application.services.trash_service import TrashService


class TestTrashService:
    def test_deleted_dir_path(self, tmp_path):
        from iPhoto.config import RECENTLY_DELETED_DIR_NAME

        svc = TrashService()
        expected = tmp_path / RECENTLY_DELETED_DIR_NAME
        assert svc.deleted_dir_path(tmp_path) == expected

    def test_relative_deleted_album_path(self, tmp_path):
        svc = TrashService()
        lib = tmp_path / "lib"
        lib.mkdir()
        trash = lib / ".deleted"
        trash.mkdir()
        rel = svc.relative_deleted_album_path(trash, lib)
        assert rel == ".deleted"

    def test_relative_deleted_album_path_outside_returns_none(self, tmp_path):
        svc = TrashService()
        lib = tmp_path / "lib"
        lib.mkdir()
        outside = tmp_path / "other"
        outside.mkdir()
        rel = svc.relative_deleted_album_path(outside, lib)
        assert rel is None

    def test_is_trash_root_no_library(self, tmp_path):
        from iPhoto.config import RECENTLY_DELETED_DIR_NAME

        svc = TrashService()
        trash = tmp_path / RECENTLY_DELETED_DIR_NAME
        assert svc.is_trash_root(trash, None) is True
        assert svc.is_trash_root(tmp_path / "other", None) is False

    def test_restore_origin_is_in_library_true(self, tmp_path):
        svc = TrashService()
        lib = tmp_path / "lib"
        lib.mkdir()
        dest = lib / "album" / "photo.jpg"
        dest.parent.mkdir(parents=True)
        dest.touch()
        assert svc.restore_origin_is_in_library(dest, lib) is True

    def test_restore_origin_is_in_library_false_when_outside(self, tmp_path):
        svc = TrashService()
        lib = tmp_path / "lib"
        lib.mkdir()
        outside = tmp_path / "outside" / "photo.jpg"
        outside.parent.mkdir(parents=True)
        outside.touch()
        assert svc.restore_origin_is_in_library(outside, lib) is False


# ---------------------------------------------------------------------------
# FsScanner (infrastructure)
# ---------------------------------------------------------------------------
from iPhoto.infrastructure.scan.fs_scanner import FsScanner


class TestFsScanner:
    def test_scan_returns_rows(self, tmp_path):
        (tmp_path / "photo.jpg").write_bytes(b"fake jpg")
        scanner = FsScanner()
        with patch(
            "iPhoto.io.scanner_adapter.scan_album",
            return_value=[{"rel": "photo.jpg"}],
        ) as mock_scan:
            rows = scanner.scan(tmp_path, ["*.jpg"], [])
        assert rows == [{"rel": "photo.jpg"}]
        mock_scan.assert_called_once()


# ---------------------------------------------------------------------------
# ScanResultPersister (infrastructure)
# ---------------------------------------------------------------------------
from iPhoto.infrastructure.scan.scan_result_persister import ScanResultPersister


class TestScanResultPersister:
    def test_persist_calls_index_and_links(self, tmp_path):
        persister = ScanResultPersister()
        with (
            patch(
                "iPhoto.index_sync_service.update_index_snapshot"
            ) as mock_update,
            patch(
                "iPhoto.index_sync_service.ensure_links"
            ) as mock_links,
        ):
            rows = [{"rel": "photo.jpg"}]
            persister.persist(tmp_path, rows)
        mock_update.assert_called_once_with(tmp_path, rows, library_root=None)
        mock_links.assert_called_once_with(tmp_path, rows, library_root=None)

    def test_persist_uses_album_rows_for_links(self, tmp_path):
        persister = ScanResultPersister()
        with (
            patch(
                "iPhoto.index_sync_service.update_index_snapshot"
            ),
            patch(
                "iPhoto.index_sync_service.ensure_links"
            ) as mock_links,
        ):
            rows = [{"rel": "Album/photo.jpg"}]
            album_rows = [{"rel": "photo.jpg"}]
            persister.persist(tmp_path, rows, album_rows=album_rows)
        mock_links.assert_called_once_with(tmp_path, album_rows, library_root=None)


# ---------------------------------------------------------------------------
# LivePairingReader (infrastructure)
# ---------------------------------------------------------------------------
from iPhoto.infrastructure.scan.live_pairing_reader import LivePairingReader


class TestLivePairingReader:
    def test_read_album_rows_strips_prefix(self, tmp_path):
        lib = tmp_path / "lib"
        lib.mkdir()
        album = lib / "Album"
        album.mkdir()

        mock_store = MagicMock()
        mock_store.read_album_assets.return_value = [
            {"rel": "Album/photo.jpg"},
            {"rel": "Album/video.mp4"},
        ]

        with patch(
            "iPhoto.cache.index_store.get_global_repository",
            return_value=mock_store,
        ):
            reader = LivePairingReader()
            rows = reader.read_album_rows(album, library_root=lib)

        rels = [r["rel"] for r in rows]
        assert "photo.jpg" in rels
        assert "video.mp4" in rels
        # Prefix should be stripped
        assert not any(r.startswith("Album/") for r in rels)

    def test_read_album_rows_no_library(self, tmp_path):
        mock_store = MagicMock()
        mock_store.read_all.return_value = [{"rel": "photo.jpg"}]

        with patch(
            "iPhoto.cache.index_store.get_global_repository",
            return_value=mock_store,
        ):
            reader = LivePairingReader()
            rows = reader.read_album_rows(tmp_path, library_root=None)

        assert rows == [{"rel": "photo.jpg"}]


# ---------------------------------------------------------------------------
# LibraryManager delegation smoke test
# ---------------------------------------------------------------------------

class TestLibraryManagerDelegation:
    """Verify that LibraryManager correctly wires up Phase 2 services."""

    def test_library_manager_has_phase2_services(self):
        """LibraryManager must expose the four Phase 2 service attributes."""
        from iPhoto.application.services.library_tree_service import LibraryTreeService
        from iPhoto.application.services.library_scan_service import LibraryScanService
        from iPhoto.application.services.library_watch_service import LibraryWatchService
        from iPhoto.application.services.trash_service import TrashService
        from iPhoto.infrastructure.watcher.qt_library_watcher import QtLibraryWatcher

        # Import without instantiating a QObject (which needs a QApplication)
        import iPhoto.library.manager as mgr_module

        # Confirm the imports exist in the module namespace
        assert hasattr(mgr_module, "LibraryTreeService")
        assert hasattr(mgr_module, "LibraryScanService")
        assert hasattr(mgr_module, "LibraryWatchService")
        assert hasattr(mgr_module, "TrashService")
        assert hasattr(mgr_module, "QtLibraryWatcher")
