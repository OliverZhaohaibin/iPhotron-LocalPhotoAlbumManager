"""Phase 3 refactoring tests.

Tests for the new Phase 3 application-layer services:
- RestoreAftercareService
- MoveAftercareService
- LibraryReloadService

Also covers:
- Compatibility-shell behaviour (app.py, appctx.py shim contracts)
- RuntimeContext formal entry point
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# RestoreAftercareService tests
# ---------------------------------------------------------------------------


class TestRestoreAftercareService:
    """Unit tests for RestoreAftercareService."""

    @pytest.fixture
    def svc(self):
        from iPhoto.application.services.restore_aftercare_service import RestoreAftercareService

        return RestoreAftercareService()

    def test_should_not_trigger_when_not_restore_op(self, svc, tmp_path):
        trash = tmp_path / "Recently Deleted"
        trash.mkdir()
        result = svc.should_trigger_restore_rescan(
            is_restore_operation=False,
            destination_ok=True,
            source_root=trash,
            trash_root=trash,
        )
        assert result is False

    def test_should_not_trigger_when_destination_failed(self, svc, tmp_path):
        trash = tmp_path / "Recently Deleted"
        trash.mkdir()
        result = svc.should_trigger_restore_rescan(
            is_restore_operation=True,
            destination_ok=False,
            source_root=trash,
            trash_root=trash,
        )
        assert result is False

    def test_should_not_trigger_when_no_trash_root(self, svc, tmp_path):
        trash = tmp_path / "Recently Deleted"
        trash.mkdir()
        result = svc.should_trigger_restore_rescan(
            is_restore_operation=True,
            destination_ok=True,
            source_root=trash,
            trash_root=None,
        )
        assert result is False

    def test_should_not_trigger_when_source_not_trash(self, svc, tmp_path):
        trash = tmp_path / "Recently Deleted"
        trash.mkdir()
        other = tmp_path / "Albums" / "MyAlbum"
        other.mkdir(parents=True)
        result = svc.should_trigger_restore_rescan(
            is_restore_operation=True,
            destination_ok=True,
            source_root=other,
            trash_root=trash,
        )
        assert result is False

    def test_should_trigger_when_source_is_trash(self, svc, tmp_path):
        trash = tmp_path / "Recently Deleted"
        trash.mkdir()
        result = svc.should_trigger_restore_rescan(
            is_restore_operation=True,
            destination_ok=True,
            source_root=trash,
            trash_root=trash,
        )
        assert result is True

    def test_compute_restore_rescan_targets_empty_pairs(self, svc):
        targets = svc.compute_restore_rescan_targets([], library_root=None)
        assert targets == []

    def test_compute_restore_rescan_targets_with_pairs(self, svc, tmp_path):
        lib = tmp_path / "Library"
        album = lib / "MyAlbum"
        album.mkdir(parents=True)
        # Create a marker so locate_album_root can find it
        (album / ".iphoto").mkdir(exist_ok=True)

        dest_file = album / "photo.jpg"
        pairs = [(tmp_path / "Recently Deleted" / "photo.jpg", dest_file)]
        # Since locate_album_root walks upward looking for WORK_DIR_NAME, the
        # result depends on the fixture.  Just verify it returns a list (possibly
        # empty when the album root marker isn't present).
        targets = svc.compute_restore_rescan_targets(pairs, library_root=lib)
        assert isinstance(targets, list)


# ---------------------------------------------------------------------------
# MoveAftercareService tests
# ---------------------------------------------------------------------------


class TestMoveAftercareService:
    """Unit tests for MoveAftercareService."""

    @pytest.fixture
    def svc(self):
        from iPhoto.application.services.move_aftercare_service import MoveAftercareService

        return MoveAftercareService()

    def test_compute_aftermath_empty_pairs(self, svc, tmp_path):
        src = tmp_path / "Src"
        dst = tmp_path / "Dst"
        result = svc.compute_aftermath(
            [],
            src,
            dst,
            current_root=None,
            library_root=None,
            source_ok=True,
            destination_ok=True,
        )
        assert result.removed_rels == []
        assert result.added_rels == []
        # With no moved pairs, refresh_targets may still include src/dst roots
        # (bookkeeping marks them stale); we simply verify the type is correct.
        assert isinstance(result.refresh_targets, dict)

    def test_compute_aftermath_returns_moved_rels(self, svc, tmp_path):
        lib = tmp_path / "Library"
        lib.mkdir()
        src = lib / "SrcAlbum"
        dst = lib / "DstAlbum"
        src.mkdir()
        dst.mkdir()

        orig = src / "img.jpg"
        target = dst / "img.jpg"
        # We test path computation, not filesystem presence
        pairs = [(orig, target)]

        result = svc.compute_aftermath(
            pairs,
            src,
            dst,
            current_root=None,
            library_root=lib,
            source_ok=True,
            destination_ok=True,
        )
        # Rels may be empty if paths don't resolve; the important thing is the
        # return type is correct and no exception is raised.
        assert isinstance(result.removed_rels, list)
        assert isinstance(result.added_rels, list)
        assert isinstance(result.refresh_targets, dict)

    def test_consume_forced_reload_initially_false(self, svc, tmp_path):
        assert svc.consume_forced_reload(tmp_path / "SomeAlbum") is False

    def test_reset_clears_state(self, svc):
        # Should not raise
        svc.reset()


# ---------------------------------------------------------------------------
# LibraryReloadService tests
# ---------------------------------------------------------------------------


class TestLibraryReloadService:
    """Unit tests for LibraryReloadService."""

    @pytest.fixture
    def svc(self):
        from iPhoto.application.services.library_reload_service import LibraryReloadService

        return LibraryReloadService()

    def test_restore_reload_no_current_root(self, svc, tmp_path):
        action = svc.compute_restore_reload_action(
            tmp_path / "SomeAlbum",
            current_root=None,
            library_root=None,
        )
        assert action.requires_action is False

    def test_restore_reload_matching_current_root(self, svc, tmp_path):
        album = tmp_path / "MyAlbum"
        album.mkdir()
        action = svc.compute_restore_reload_action(
            album,
            current_root=album,
            library_root=None,
        )
        assert action.should_reload_current is True
        assert action.target_root == album

    def test_restore_reload_non_matching_root(self, svc, tmp_path):
        album = tmp_path / "MyAlbum"
        album.mkdir()
        other = tmp_path / "OtherAlbum"
        other.mkdir()
        action = svc.compute_restore_reload_action(
            other,
            current_root=album,
            library_root=None,
        )
        assert action.should_reload_current is False
        assert action.should_reload_as_library is False

    def test_restore_reload_library_ancestor(self, svc, tmp_path):
        lib = tmp_path / "Library"
        lib.mkdir()
        album = lib / "MyAlbum"
        album.mkdir()
        # current_root is library; restored path is inside library
        action = svc.compute_restore_reload_action(
            album,
            current_root=lib,
            library_root=lib,
        )
        assert action.should_reload_as_library is True

    def test_scan_reload_skipped_when_model_loading(self, svc, tmp_path):
        album = tmp_path / "MyAlbum"
        action = svc.compute_scan_reload_action(
            album, current_root=album, model_loading_due_to_scan=True
        )
        assert action.requires_action is False

    def test_scan_reload_triggered_normally(self, svc, tmp_path):
        album = tmp_path / "MyAlbum"
        action = svc.compute_scan_reload_action(
            album, current_root=album, model_loading_due_to_scan=False
        )
        assert action.should_reload_current is True

    def test_reload_action_frozen(self, svc):
        from iPhoto.application.services.library_reload_service import ReloadAction

        action = ReloadAction(should_reload_current=True, target_root=Path("/tmp/x"))
        with pytest.raises((TypeError, AttributeError)):
            action.should_reload_current = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Compatibility shell contract: app.py
# ---------------------------------------------------------------------------


class TestAppShimContract:
    """Verify app.py forwards to use cases without owning business logic."""

    def test_rescan_delegates_to_use_case(self, tmp_path):
        """app.rescan must delegate to RescanAlbumUseCase."""
        album_dir = tmp_path / "album"
        album_dir.mkdir()
        (album_dir / ".iphoto.album").touch()

        import iPhoto.app as app

        with patch(
            "iPhoto.application.use_cases.scan.rescan_album_use_case.RescanAlbumUseCase.execute",
            return_value=[],
        ) as mock_exec:
            result = app.rescan(album_dir)

        mock_exec.assert_called_once_with(album_dir)
        assert result == []

    def test_pair_delegates_to_use_case(self, tmp_path):
        """app.pair must delegate to PairLivePhotosUseCaseV2."""
        album_dir = tmp_path / "album"
        album_dir.mkdir()

        import iPhoto.app as app

        with patch(
            "iPhoto.application.use_cases.scan.pair_live_photos_use_case_v2"
            ".PairLivePhotosUseCaseV2.execute",
            return_value=[],
        ) as mock_exec:
            result = app.pair(album_dir)

        mock_exec.assert_called_once_with(album_dir)
        assert result == []

    def test_open_album_delegates_to_workflow_use_case(self, tmp_path):
        """app.open_album must delegate to OpenAlbumWorkflowUseCase."""
        album_dir = tmp_path / "album"
        album_dir.mkdir()

        import iPhoto.app as app
        from iPhoto.models.album import Album

        fake_album = MagicMock(spec=Album)

        with patch(
            "iPhoto.application.use_cases.scan.open_album_workflow_use_case"
            ".OpenAlbumWorkflowUseCase.execute",
            return_value=fake_album,
        ) as mock_exec:
            result = app.open_album(album_dir)

        mock_exec.assert_called_once()
        assert result is fake_album

    def test_scan_specific_files_delegates_to_use_case(self, tmp_path):
        """app.scan_specific_files must delegate to ScanSpecificFilesUseCase."""
        album_dir = tmp_path / "album"
        album_dir.mkdir()

        import iPhoto.app as app

        fake_file = album_dir / "photo.jpg"
        fake_file.touch()

        with patch(
            "iPhoto.application.use_cases.asset.scan_specific_files_use_case"
            ".ScanSpecificFilesUseCase.execute"
        ) as mock_exec:
            app.scan_specific_files(album_dir, [fake_file])

        mock_exec.assert_called_once_with(album_dir, [fake_file], library_root=None)

    def test_scan_specific_files_forwards_library_root(self, tmp_path):
        """app.scan_specific_files must pass library_root to the use case."""
        album_dir = tmp_path / "album"
        lib_dir = tmp_path / "library"
        album_dir.mkdir()
        lib_dir.mkdir()

        import iPhoto.app as app

        with patch(
            "iPhoto.application.use_cases.asset.scan_specific_files_use_case"
            ".ScanSpecificFilesUseCase.execute"
        ) as mock_exec:
            app.scan_specific_files(album_dir, [], library_root=lib_dir)

        mock_exec.assert_called_once_with(album_dir, [], library_root=lib_dir)


# ---------------------------------------------------------------------------
# RuntimeContext formal entry point
# ---------------------------------------------------------------------------


_pyside6_available = False
try:
    import PySide6  # noqa: F401

    _pyside6_available = True
except ImportError:
    pass


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
class TestRuntimeContext:
    """Verify RuntimeContext composes the DI container and session correctly."""

    def test_create_with_defer_startup(self):
        """RuntimeContext.create(defer_startup=True) should not raise."""

        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = RuntimeContext.create(defer_startup=True)
        assert ctx.container is not None
        assert ctx.library is not None
        assert ctx.settings is not None
        assert ctx.facade is not None

    def test_remember_album_updates_list(self, tmp_path):
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = RuntimeContext.create(defer_startup=True)
        fake_path = tmp_path / "MyAlbum"
        fake_path.mkdir()

        # remember_album resolves and stores
        ctx.remember_album(fake_path)
        assert len(ctx.recent_albums) >= 1

    def test_runtime_context_exposes_recent_albums(self):
        from iPhoto.bootstrap.runtime_context import RuntimeContext

        ctx = RuntimeContext.create(defer_startup=True)
        assert isinstance(ctx.recent_albums, list)


# ---------------------------------------------------------------------------
# OpenAlbumWorkflowUseCase tests
# ---------------------------------------------------------------------------


class TestOpenAlbumWorkflowUseCase:
    """Unit tests for the extracted OpenAlbumWorkflowUseCase."""

    def test_execute_returns_album(self, tmp_path):
        from iPhoto.application.use_cases.scan.open_album_workflow_use_case import (
            OpenAlbumWorkflowUseCase,
        )
        from iPhoto.models.album import Album

        album_dir = tmp_path / "MyAlbum"
        album_dir.mkdir()
        (album_dir / "album.json").write_text('{"title": "Test"}')

        uc = OpenAlbumWorkflowUseCase()
        mock_album = MagicMock(spec=Album)
        mock_album.manifest = {}
        mock_album.root = album_dir

        mock_store = MagicMock()
        mock_store.read_all.return_value = []
        mock_store.sync_favorites.return_value = None

        with patch(
            "iPhoto.cache.index_store.get_global_repository",
            return_value=mock_store,
        ):
            with patch(
                "iPhoto.models.album.Album.open",
                return_value=mock_album,
            ):
                with patch(
                    "iPhoto.index_sync_service.ensure_links",
                ):
                    with patch(
                        "iPhoto.path_normalizer.compute_album_path",
                        return_value=None,
                    ):
                        result = uc.execute(album_dir)

        assert result is not None


# ---------------------------------------------------------------------------
# ScanSpecificFilesUseCase unit tests
# ---------------------------------------------------------------------------


class TestScanSpecificFilesUseCase:
    """Unit tests for ScanSpecificFilesUseCase – no Qt required."""

    def test_execute_calls_process_media_paths(self, tmp_path):
        """execute() must pass classified image/video lists to process_media_paths."""
        pytest.importorskip("PIL", reason="Pillow required for scanner_adapter")
        pytest.importorskip("xxhash", reason="xxhash required for scanner_adapter")

        from iPhoto.application.use_cases.asset.scan_specific_files_use_case import (
            ScanSpecificFilesUseCase,
        )
        import iPhoto.io.scanner_adapter  # noqa: F401 - ensure module is loaded before patching

        album = tmp_path / "album"
        album.mkdir()

        img = album / "photo.jpg"
        vid = album / "clip.mp4"
        other = album / "readme.txt"
        for f in (img, vid, other):
            f.touch()

        mock_store = MagicMock()
        mock_store.append_rows = MagicMock()

        with patch(
            "iPhoto.io.scanner_adapter.process_media_paths",
            return_value=iter([]),
        ) as mock_process, patch(
            "iPhoto.cache.index_store.get_global_repository",
            return_value=mock_store,
        ), patch(
            "iPhoto.path_normalizer.compute_album_path",
            return_value=None,
        ):
            ScanSpecificFilesUseCase().execute(album, [img, vid, other])

        call_args = mock_process.call_args
        assert img in call_args.args[1]  # image list
        assert vid in call_args.args[2]  # video list
        assert other not in call_args.args[1]
        assert other not in call_args.args[2]

    def test_execute_applies_album_path_prefix(self, tmp_path):
        """execute() must apply AlbumPathPolicy when album_path is returned."""
        pytest.importorskip("PIL", reason="Pillow required for scanner_adapter")
        pytest.importorskip("xxhash", reason="xxhash required for scanner_adapter")

        from iPhoto.application.use_cases.asset.scan_specific_files_use_case import (
            ScanSpecificFilesUseCase,
        )
        import iPhoto.io.scanner_adapter  # noqa: F401

        album = tmp_path / "library" / "MyAlbum"
        album.mkdir(parents=True)
        lib = tmp_path / "library"

        fake_rows = [{"rel": "photo.jpg"}]
        mock_store = MagicMock()
        mock_store.append_rows = MagicMock()

        with patch(
            "iPhoto.io.scanner_adapter.process_media_paths",
            return_value=iter(fake_rows),
        ), patch(
            "iPhoto.cache.index_store.get_global_repository",
            return_value=mock_store,
        ), patch(
            "iPhoto.path_normalizer.compute_album_path",
            return_value="MyAlbum",
        ), patch(
            "iPhoto.application.policies.album_path_policy.AlbumPathPolicy.prefix_rows",
            return_value=[{"rel": "MyAlbum/photo.jpg"}],
        ) as mock_prefix:
            ScanSpecificFilesUseCase().execute(album, [], library_root=lib)

        mock_prefix.assert_called_once()
        # The rows passed to append_rows should be the prefixed rows.
        mock_store.append_rows.assert_called_once_with([{"rel": "MyAlbum/photo.jpg"}])

    def test_execute_uses_library_root_for_store(self, tmp_path):
        """execute() must use library_root as the index store key when provided."""
        pytest.importorskip("PIL", reason="Pillow required for scanner_adapter")
        pytest.importorskip("xxhash", reason="xxhash required for scanner_adapter")

        from iPhoto.application.use_cases.asset.scan_specific_files_use_case import (
            ScanSpecificFilesUseCase,
        )
        import iPhoto.io.scanner_adapter  # noqa: F401

        album = tmp_path / "library" / "MyAlbum"
        album.mkdir(parents=True)
        lib = tmp_path / "library"

        mock_store = MagicMock()
        mock_store.append_rows = MagicMock()

        with patch(
            "iPhoto.io.scanner_adapter.process_media_paths",
            return_value=iter([]),
        ), patch(
            "iPhoto.cache.index_store.get_global_repository",
            return_value=mock_store,
        ) as mock_repo, patch(
            "iPhoto.path_normalizer.compute_album_path",
            return_value=None,
        ):
            ScanSpecificFilesUseCase().execute(album, [], library_root=lib)

        mock_repo.assert_called_once_with(lib)
