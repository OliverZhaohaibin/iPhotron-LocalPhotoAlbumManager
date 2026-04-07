"""Step 2 closure tests.

Covers the items identified in evaluate step2.md that need concrete
unit and integration coverage:

- RescanAlbumUseCase is a pure orchestrator (mocks verify delegation)
- MoveBookkeepingService.compute_refresh_targets / compute_move_rels
- app.py compat bridge uses AlbumPathPolicy (no inline f-string prefix)
- LibraryScopePolicy.paths_equal
- PersistScanResultUseCase album_path strip behaviour
- Integration: global db + nested album, restore chain
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch
import pytest


# ---------------------------------------------------------------------------
# RescanAlbumUseCase – pure orchestration (mock delegates)
# ---------------------------------------------------------------------------

class TestRescanAlbumUseCaseOrchestration:
    """Verify that RescanAlbumUseCase only orchestrates – no scanner/db detail."""

    def _make_use_case(self, library_root=None):
        from iPhoto.application.use_cases.scan.rescan_album_use_case import RescanAlbumUseCase

        uc = RescanAlbumUseCase(library_root_getter=lambda: library_root)
        # Replace all sub-delegates with mocks
        uc._scanner = MagicMock()
        uc._scanner.scan.return_value = [{"rel": "photo.jpg"}]
        uc._load_index_uc = MagicMock()
        uc._load_index_uc.execute.return_value = {}
        uc._merge_trash_uc = MagicMock()
        uc._merge_trash_uc.execute.side_effect = lambda rows, *a, **kw: rows
        uc._persist_uc = MagicMock()
        return uc

    def test_calls_load_index_use_case(self, tmp_path):
        uc = self._make_use_case()
        (tmp_path / ".iPhoto").mkdir()
        (tmp_path / "manifest.json").write_text("{}")
        uc.execute(tmp_path)
        uc._load_index_uc.execute.assert_called_once()

    def test_calls_scanner(self, tmp_path):
        uc = self._make_use_case()
        (tmp_path / ".iPhoto").mkdir()
        (tmp_path / "manifest.json").write_text("{}")
        uc.execute(tmp_path)
        uc._scanner.scan.assert_called_once()

    def test_calls_merge_trash_use_case(self, tmp_path):
        uc = self._make_use_case()
        (tmp_path / ".iPhoto").mkdir()
        (tmp_path / "manifest.json").write_text("{}")
        uc.execute(tmp_path)
        uc._merge_trash_uc.execute.assert_called_once()

    def test_calls_persist_use_case(self, tmp_path):
        uc = self._make_use_case()
        (tmp_path / ".iPhoto").mkdir()
        (tmp_path / "manifest.json").write_text("{}")
        uc.execute(tmp_path)
        uc._persist_uc.execute.assert_called_once()

    def test_no_direct_scan_album_import(self):
        """The use case must not contain a direct `scan_album` reference."""
        import inspect
        from iPhoto.application.use_cases.scan.rescan_album_use_case import RescanAlbumUseCase

        source = inspect.getsource(RescanAlbumUseCase)
        assert "scan_album" not in source, (
            "RescanAlbumUseCase should delegate to FsScanner, not call scan_album directly"
        )

    def test_no_direct_update_index_snapshot_import(self):
        """The use case must not call _update_index_snapshot / _ensure_links directly in execute."""
        import inspect
        from iPhoto.application.use_cases.scan.rescan_album_use_case import RescanAlbumUseCase

        source = inspect.getsource(RescanAlbumUseCase.execute)
        assert "_update_index_snapshot" not in source
        assert "_ensure_links" not in source

    def test_uses_fs_scanner(self):
        """RescanAlbumUseCase must have an FsScanner as _scanner."""
        from iPhoto.application.use_cases.scan.rescan_album_use_case import RescanAlbumUseCase
        from iPhoto.infrastructure.scan.fs_scanner import FsScanner

        uc = RescanAlbumUseCase()
        assert isinstance(uc._scanner, FsScanner)

    def test_uses_persist_scan_result_use_case(self):
        """RescanAlbumUseCase must have a PersistScanResultUseCase as _persist_uc."""
        from iPhoto.application.use_cases.scan.rescan_album_use_case import RescanAlbumUseCase
        from iPhoto.application.use_cases.scan.persist_scan_result_use_case import (
            PersistScanResultUseCase,
        )

        uc = RescanAlbumUseCase()
        assert isinstance(uc._persist_uc, PersistScanResultUseCase)

    def test_prefix_rows_passed_to_persist(self, tmp_path):
        """With a library root the rows passed to persist must carry the album prefix."""
        lib = tmp_path / "lib"
        album = lib / "AlbumA"
        album.mkdir(parents=True)
        (album / ".iPhoto").mkdir()
        (album / "manifest.json").write_text("{}")

        uc = self._make_use_case(library_root=lib)

        captured: list = []

        def _capture_persist(root, rows, *, library_root=None, album_path=None):
            captured.append((list(rows), album_path))

        uc._persist_uc.execute.side_effect = _capture_persist
        uc.execute(album)

        assert captured, "PersistScanResultUseCase was not called"
        rows_passed, ap = captured[0]
        assert ap == "AlbumA", f"Expected album_path='AlbumA', got {ap!r}"
        # All rows should carry the library prefix
        for row in rows_passed:
            assert row["rel"].startswith("AlbumA/"), (
                f"Row rel {row['rel']!r} missing album prefix"
            )


# ---------------------------------------------------------------------------
# PersistScanResultUseCase – album_path strip
# ---------------------------------------------------------------------------

class TestPersistScanResultUseCaseAlbumPath:
    def _make(self):
        from iPhoto.application.use_cases.scan.persist_scan_result_use_case import (
            PersistScanResultUseCase,
        )

        update_calls: list = []
        ensure_calls: list = []

        def _update(root, rows, lib):
            update_calls.append(list(rows))

        def _ensure(root, rows, lib):
            ensure_calls.append(list(rows))

        uc = PersistScanResultUseCase(
            update_index_snapshot=_update,
            ensure_links=_ensure,
        )
        return uc, update_calls, ensure_calls

    def test_no_album_path_passes_rows_as_is(self, tmp_path):
        uc, update, ensure = self._make()
        rows = [{"rel": "photo.jpg"}]
        uc.execute(tmp_path, rows)
        assert update[0] == rows
        assert ensure[0] == rows

    def test_album_path_strips_before_ensure_links(self, tmp_path):
        uc, update, ensure = self._make()
        rows = [{"rel": "AlbumA/photo.jpg"}]
        uc.execute(tmp_path, rows, album_path="AlbumA")
        # update_index_snapshot receives the library-scoped rows (unchanged)
        assert update[0] == rows
        # ensure_links receives the stripped album-relative rows
        assert ensure[0] == [{"rel": "photo.jpg"}]

    def test_album_path_nested_strip(self, tmp_path):
        uc, update, ensure = self._make()
        rows = [{"rel": "Parent/Child/shot.jpg"}]
        uc.execute(tmp_path, rows, album_path="Parent/Child")
        assert ensure[0] == [{"rel": "shot.jpg"}]


# ---------------------------------------------------------------------------
# app.py compat bridge – uses AlbumPathPolicy, no inline f-string
# ---------------------------------------------------------------------------

class TestAppCompatBridgePathPolicy:
    def test_open_album_no_inline_f_string(self):
        import inspect
        import iPhoto.app as app_mod

        source = inspect.getsource(app_mod.open_album)
        assert 'f"{album_path}/' not in source, (
            "open_album must not use inline f-string prefix; use AlbumPathPolicy"
        )

    def test_scan_specific_files_no_inline_f_string(self):
        import inspect
        import iPhoto.app as app_mod

        source = inspect.getsource(app_mod.scan_specific_files)
        assert 'f"{album_path}/' not in source, (
            "scan_specific_files must not use inline f-string prefix; use AlbumPathPolicy"
        )

    def test_open_album_uses_album_path_policy(self):
        import inspect
        import iPhoto.app as app_mod

        source = inspect.getsource(app_mod)
        assert "AlbumPathPolicy" in source or "_AlbumPathPolicy" in source, (
            "app.py must import and use AlbumPathPolicy for path operations"
        )

    def test_open_album_prefix_rows_with_policy(self, tmp_path):
        """open_album should prefix scan rows via AlbumPathPolicy, not manually."""
        lib = tmp_path / "lib"
        album = lib / "Photos"
        album.mkdir(parents=True)
        (album / ".iPhoto").mkdir()
        (album / "manifest.json").write_text("{}")

        mock_store = MagicMock()
        mock_store.count.return_value = 0
        mock_store.write_rows = MagicMock()
        mock_store.sync_favorites = MagicMock()

        scan_rows = [{"rel": "a.jpg"}, {"rel": "b.jpg"}]
        written: list = []

        def _write_rows(rows):
            written.extend(rows)

        mock_store.write_rows.side_effect = _write_rows

        with patch("iPhoto.app.get_global_repository", return_value=mock_store), \
             patch("iPhoto.io.scanner_adapter.scan_album", return_value=iter(scan_rows)), \
             patch("iPhoto.app._ensure_links"):
            import iPhoto.app as app_mod
            app_mod.open_album(album, autoscan=True, library_root=lib, hydrate_index=False)

        # All written rows should have the "Photos/" prefix
        for row in written:
            assert row["rel"].startswith("Photos/"), (
                f"Expected row rel to start with 'Photos/', got {row['rel']!r}"
            )

    def test_scan_specific_files_prefix_rows_with_policy(self, tmp_path):
        """scan_specific_files should prefix rows via AlbumPathPolicy."""
        lib = tmp_path / "lib"
        album = lib / "Portraits"
        album.mkdir(parents=True)
        photo = album / "shot.jpg"
        photo.touch()

        mock_store = MagicMock()
        appended: list = []
        mock_store.append_rows.side_effect = lambda rows: appended.extend(rows)

        scan_rows = [{"rel": "shot.jpg"}]

        with patch("iPhoto.app.get_global_repository", return_value=mock_store), \
             patch("iPhoto.io.scanner_adapter.process_media_paths", return_value=iter(scan_rows)):
            import iPhoto.app as app_mod
            app_mod.scan_specific_files(album, [photo], library_root=lib)

        for row in appended:
            assert row["rel"].startswith("Portraits/"), (
                f"Expected 'Portraits/' prefix, got {row['rel']!r}"
            )


# ---------------------------------------------------------------------------
# LibraryScopePolicy.paths_equal
# ---------------------------------------------------------------------------

class TestLibraryScopePolicyPathsEqual:
    def test_same_path_equal(self, tmp_path):
        from iPhoto.application.policies.library_scope_policy import LibraryScopePolicy

        p = LibraryScopePolicy()
        assert p.paths_equal(tmp_path, tmp_path) is True

    def test_different_paths_not_equal(self, tmp_path):
        from iPhoto.application.policies.library_scope_policy import LibraryScopePolicy

        p = LibraryScopePolicy()
        other = tmp_path / "sub"
        other.mkdir()
        assert p.paths_equal(tmp_path, other) is False

    def test_resolved_equal(self, tmp_path):
        from iPhoto.application.policies.library_scope_policy import LibraryScopePolicy

        p = LibraryScopePolicy()
        # Both forms of the same path should be equal
        assert p.paths_equal(tmp_path / ".", tmp_path) is True


# ---------------------------------------------------------------------------
# MoveBookkeepingService.compute_move_rels
# ---------------------------------------------------------------------------

class TestMoveBookkeepingComputeMoveRels:
    def test_returns_correct_rels(self, tmp_path):
        from iPhoto.application.services.move_bookkeeping_service import MoveBookkeepingService

        lib = tmp_path / "lib"
        src_album = lib / "Album"
        dst_album = lib / "Trash"
        photo_src = src_album / "a.jpg"
        photo_dst = dst_album / "a.jpg"
        src_album.mkdir(parents=True)
        dst_album.mkdir(parents=True)
        photo_src.touch()
        photo_dst.touch()

        svc = MoveBookkeepingService()
        removed, added = svc.compute_move_rels(
            [(photo_src, photo_dst)], lib, src_album, dst_album
        )
        assert len(removed) == 1
        assert len(added) == 1
        assert removed[0] == "Album/a.jpg"
        assert added[0] == "Trash/a.jpg"

    def test_no_library_root_uses_album_roots(self, tmp_path):
        from iPhoto.application.services.move_bookkeeping_service import MoveBookkeepingService

        src_album = tmp_path / "Album"
        dst_album = tmp_path / "Trash"
        photo_src = src_album / "a.jpg"
        photo_dst = dst_album / "a.jpg"
        src_album.mkdir(parents=True)
        dst_album.mkdir(parents=True)
        photo_src.touch()
        photo_dst.touch()

        svc = MoveBookkeepingService()
        removed, added = svc.compute_move_rels(
            [(photo_src, photo_dst)], None, src_album, dst_album
        )
        assert removed[0] == "a.jpg"
        assert added[0] == "a.jpg"


# ---------------------------------------------------------------------------
# MoveBookkeepingService.compute_refresh_targets
# ---------------------------------------------------------------------------

class TestMoveBookkeepingComputeRefreshTargets:
    def _svc(self):
        from iPhoto.application.services.move_bookkeeping_service import MoveBookkeepingService
        return MoveBookkeepingService()

    def test_source_and_destination_in_targets(self, tmp_path):
        src = tmp_path / "Src"
        dst = tmp_path / "Dst"
        src.mkdir()
        dst.mkdir()

        svc = self._svc()
        targets = svc.compute_refresh_targets(
            [], src, dst, None, None,
            source_ok=True, destination_ok=True
        )
        paths = {t[0] for t in targets.values()}
        assert src in paths
        assert dst in paths

    def test_source_not_included_when_source_ok_false(self, tmp_path):
        src = tmp_path / "Src"
        dst = tmp_path / "Dst"
        src.mkdir()
        dst.mkdir()

        svc = self._svc()
        targets = svc.compute_refresh_targets(
            [], src, dst, None, None,
            source_ok=False, destination_ok=True
        )
        paths = {t[0] for t in targets.values()}
        assert src not in paths

    def test_should_restart_true_when_current_matches_destination(self, tmp_path):
        src = tmp_path / "Src"
        dst = tmp_path / "Dst"
        src.mkdir()
        dst.mkdir()

        svc = self._svc()
        targets = svc.compute_refresh_targets(
            [], src, dst, dst, None,
            source_ok=True, destination_ok=True
        )
        # dst should have should_restart=True since current_root == dst
        key = svc._key(dst)
        assert key in targets
        _, should_restart = targets[key]
        assert should_restart is True

    def test_library_root_included_when_pair_descends_into_it(self, tmp_path):
        lib = tmp_path / "lib"
        album = lib / "Album"
        album.mkdir(parents=True)
        src = album / "a.jpg"
        dst = album / "b.jpg"
        src.touch()
        dst.touch()

        svc = self._svc()
        targets = svc.compute_refresh_targets(
            [(src, dst)], album, album, None, lib,
            source_ok=True, destination_ok=True
        )
        paths = {str(t[0]) for t in targets.values()}
        assert str(lib) in paths

    def test_marks_paths_stale(self, tmp_path):
        src = tmp_path / "Src"
        dst = tmp_path / "Dst"
        src.mkdir()
        dst.mkdir()

        svc = self._svc()
        svc.compute_refresh_targets(
            [], src, dst, None, None,
            source_ok=True, destination_ok=True
        )
        # Both paths were recorded and should be stale (consume in order)
        src_stale = svc.consume_forced_reload(src)
        dst_stale = svc.consume_forced_reload(dst)
        assert src_stale is True
        assert dst_stale is True


# ---------------------------------------------------------------------------
# Integration: global db + nested album scan
# ---------------------------------------------------------------------------

class TestGlobalDbNestedAlbumIntegration:
    """End-to-end scan through RescanAlbumUseCase with real AlbumPathPolicy."""

    def test_prefix_and_strip_roundtrip(self, tmp_path):
        """prefix_rows then strip_album_prefix must be an exact inverse."""
        from iPhoto.application.policies.album_path_policy import AlbumPathPolicy

        policy = AlbumPathPolicy()
        original = [{"rel": "photo.jpg"}, {"rel": "sub/shot.jpg"}]
        prefixed = policy.prefix_rows(list(original), "Parent/Child")
        stripped = policy.strip_album_prefix(prefixed, "Parent/Child")

        assert stripped == original

    def test_nested_prefix_not_double_applied(self):
        """prefix_rows should be idempotent when prefix already present."""
        from iPhoto.application.policies.album_path_policy import AlbumPathPolicy

        policy = AlbumPathPolicy()
        rows = [{"rel": "Album/photo.jpg"}]
        result = policy.prefix_rows(rows, "Album")
        # Should not become Album/Album/photo.jpg
        assert result[0]["rel"] == "Album/photo.jpg"

    def test_rescan_use_case_passes_album_path_to_persist(self, tmp_path):
        """When library_root is set, PersistScanResultUseCase receives album_path."""
        lib = tmp_path / "lib"
        album = lib / "MyAlbum"
        album.mkdir(parents=True)
        (album / ".iPhoto").mkdir()
        (album / "manifest.json").write_text("{}")

        from iPhoto.application.use_cases.scan.rescan_album_use_case import RescanAlbumUseCase

        uc = RescanAlbumUseCase(library_root_getter=lambda: lib)
        uc._scanner = MagicMock()
        uc._scanner.scan.return_value = [{"rel": "photo.jpg"}]
        uc._load_index_uc = MagicMock()
        uc._load_index_uc.execute.return_value = {}
        uc._merge_trash_uc = MagicMock()
        uc._merge_trash_uc.execute.side_effect = lambda rows, *a, **kw: rows

        persist_calls: list = []
        uc._persist_uc = MagicMock()
        uc._persist_uc.execute.side_effect = lambda *a, **kw: persist_calls.append(kw)

        uc.execute(album)

        assert persist_calls, "PersistScanResultUseCase.execute was not called"
        assert persist_calls[0].get("album_path") == "MyAlbum"


# ---------------------------------------------------------------------------
# Integration: restore chain – metadata preserved
# ---------------------------------------------------------------------------

class TestRestoreChainIntegration:
    def test_restore_metadata_preserved_via_use_case(self, tmp_path):
        """RescanAlbumUseCase must call MergeTrashRestoreMetadataUseCase for trash albums."""
        from iPhoto.config import RECENTLY_DELETED_DIR_NAME
        from iPhoto.application.use_cases.scan.rescan_album_use_case import RescanAlbumUseCase

        trash = tmp_path / RECENTLY_DELETED_DIR_NAME
        trash.mkdir()
        (trash / "manifest.json").write_text("{}")

        uc = RescanAlbumUseCase()
        uc._scanner = MagicMock()
        uc._scanner.scan.return_value = [{"rel": "photo.jpg"}]
        uc._load_index_uc = MagicMock()
        uc._load_index_uc.execute.return_value = {}
        uc._persist_uc = MagicMock()

        merge_called_with: list = []

        def _record_merge(rows, album_root, lib_root=None):
            merge_called_with.append((album_root, lib_root))
            return rows

        uc._merge_trash_uc = MagicMock()
        uc._merge_trash_uc.execute.side_effect = _record_merge

        uc.execute(trash)

        assert merge_called_with, "MergeTrashRestoreMetadataUseCase was not called"
        called_root, _ = merge_called_with[0]
        assert called_root == trash
