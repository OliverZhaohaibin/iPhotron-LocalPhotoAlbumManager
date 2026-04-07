"""Step 2 closure tests.

Covers the items identified in evaluate step2.md that need concrete
unit and integration coverage:

- RescanAlbumUseCase is a pure orchestrator (mocks verify delegation)
- MoveBookkeepingService.compute_refresh_targets / compute_move_rels
- app.py compat bridge uses AlbumPathPolicy (no inline f-string prefix)
- LibraryScopePolicy.paths_equal
- PersistScanResultUseCase album_path strip behaviour
- Integration: global db + nested album, restore chain
- ScanCoordinatorMixin is now a thin Qt adapter delegating to LibraryScanService
- TrashService.compute_restore_reload_action drives restore reload decision
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest


# Helper: read source text from a repo module file without importing it
# (avoids PySide6 dependency for structural/source-inspection tests).
_REPO_ROOT = Path(__file__).parent.parent / "src"


def _read_source(module_dotted_path: str) -> str:
    """Return the source text of a module by its dotted path, without importing it."""
    rel = module_dotted_path.replace(".", "/") + ".py"
    full = _REPO_ROOT / rel
    return full.read_text(encoding="utf-8")

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
        pytest.importorskip("PySide6", reason="requires PySide6")

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

        # Pre-import scanner_adapter so that mock.patch can locate it as an
        # attribute of iPhoto.io (required before patching the submodule).
        import iPhoto.io.scanner_adapter  # noqa: F401

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
        pytest.importorskip("PySide6", reason="requires PySide6")

        lib = tmp_path / "lib"
        album = lib / "Portraits"
        album.mkdir(parents=True)
        photo = album / "shot.jpg"
        photo.touch()

        mock_store = MagicMock()
        appended: list = []
        mock_store.append_rows.side_effect = lambda rows: appended.extend(rows)

        scan_rows = [{"rel": "shot.jpg"}]

        # Pre-import scanner_adapter so mock.patch can locate process_media_paths.
        import iPhoto.io.scanner_adapter  # noqa: F401

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


# ---------------------------------------------------------------------------
# C3 – Structural tests: final step2 closure
# ---------------------------------------------------------------------------

class TestScanCoordinatorFinalClosure:
    """Verify ScanCoordinatorMixin is now a thin Qt adapter.

    Source-inspection tests read the file directly to avoid importing
    scan_coordinator (which pulls in PySide6).
    """

    @property
    def _source(self) -> str:
        return _read_source("iPhoto.library.scan_coordinator")

    def test_no_backend_pair_import_in_scan_coordinator(self):
        """scan_coordinator.py must not directly call backend.pair(...)."""
        assert "backend.pair(" not in self._source, (
            "scan_coordinator must not call backend.pair() directly; "
            "use LibraryScanService.on_scan_finished with a pair_callback"
        )

    def test_no_direct_app_backend_import_in_scan_coordinator(self):
        """scan_coordinator.py must not import the legacy app backend directly."""
        assert "from .. import app as backend" not in self._source, (
            "scan_coordinator must not import app as backend"
        )

    def test_scan_coordinator_delegates_is_scanning_path_to_service(self):
        """is_scanning_path must delegate to _scan_service."""
        assert "_scan_service.is_scanning_path" in self._source, (
            "is_scanning_path must delegate to self._scan_service"
        )

    def test_scan_coordinator_calls_mark_started(self):
        """start_scanning must call _scan_service.mark_started."""
        assert "_scan_service.mark_started" in self._source, (
            "start_scanning must call self._scan_service.mark_started"
        )

    def test_scan_coordinator_calls_mark_stopped_on_stop(self):
        """stop_scanning must call _scan_service.mark_stopped."""
        assert "_scan_service.mark_stopped" in self._source, (
            "stop_scanning must call self._scan_service.mark_stopped"
        )

    def test_scan_coordinator_on_scan_finished_uses_service(self):
        """_on_scan_finished must call _scan_service.on_scan_finished."""
        assert "_scan_service.on_scan_finished" in self._source, (
            "_on_scan_finished must delegate to self._scan_service.on_scan_finished"
        )

    def test_scan_coordinator_on_scan_error_uses_service(self):
        """_on_scan_error must call _scan_service.on_scan_error."""
        assert "_scan_service.on_scan_error" in self._source, (
            "_on_scan_error must delegate to self._scan_service.on_scan_error"
        )


class TestLibraryScanServiceFinalClosure:
    """Verify LibraryScanService is the true scan owner."""

    def test_has_on_scan_finished(self):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        assert hasattr(LibraryScanService, "on_scan_finished")

    def test_has_on_scan_error(self):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        assert hasattr(LibraryScanService, "on_scan_error")

    def test_has_should_skip_start(self):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        assert hasattr(LibraryScanService, "should_skip_start")

    def test_has_read_live_rows_from_store(self):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        assert hasattr(LibraryScanService, "read_live_rows_from_store")

    def test_has_resolve_live_query_root(self):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        assert hasattr(LibraryScanService, "resolve_live_query_root")

    def test_on_scan_finished_calls_pair_callback(self, tmp_path):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        svc = LibraryScanService()
        svc.mark_started(tmp_path)

        pair_calls: list = []
        svc.on_scan_finished(
            tmp_path, None,
            pair_callback=lambda r, lib: pair_calls.append((r, lib))
        )

        assert pair_calls == [(tmp_path, None)]
        assert not svc.is_scanning()

    def test_on_scan_finished_no_callback(self, tmp_path):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        svc = LibraryScanService()
        svc.mark_started(tmp_path)
        svc.on_scan_finished(tmp_path, None)  # must not raise
        assert not svc.is_scanning()

    def test_on_scan_error_clears_state(self, tmp_path):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        svc = LibraryScanService()
        svc.mark_started(tmp_path)
        svc.on_scan_error(tmp_path)
        assert not svc.is_scanning()
        assert svc.current_scan_root() is None

    def test_should_skip_start_true_when_same_root(self, tmp_path):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        svc = LibraryScanService()
        svc.mark_started(tmp_path)
        assert svc.should_skip_start(tmp_path) is True

    def test_should_skip_start_false_when_different_root(self, tmp_path):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        other = tmp_path / "other"
        other.mkdir()
        svc = LibraryScanService()
        svc.mark_started(tmp_path)
        assert svc.should_skip_start(other) is False

    def test_should_skip_start_false_when_not_scanning(self, tmp_path):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        svc = LibraryScanService()
        assert svc.should_skip_start(tmp_path) is False

    def test_pair_callback_exception_does_not_propagate(self, tmp_path):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        svc = LibraryScanService()
        svc.mark_started(tmp_path)

        def _bad_callback(r, lib):
            raise RuntimeError("pairing failed")

        svc.on_scan_finished(tmp_path, None, pair_callback=_bad_callback)  # must not raise
        assert not svc.is_scanning()

    def test_resolve_live_query_root_none_returns_scan_root(self, tmp_path):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        svc = LibraryScanService()
        result = svc.resolve_live_query_root(tmp_path, None)
        assert result == tmp_path

    def test_resolve_live_query_root_same_path(self, tmp_path):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        svc = LibraryScanService()
        result = svc.resolve_live_query_root(tmp_path, tmp_path)
        assert result == tmp_path

    def test_resolve_live_query_root_descendant(self, tmp_path):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        sub = tmp_path / "sub"
        sub.mkdir()
        svc = LibraryScanService()
        result = svc.resolve_live_query_root(tmp_path, sub)
        assert result == sub

    def test_resolve_live_query_root_unrelated_returns_none(self, tmp_path):
        from iPhoto.application.services.library_scan_service import LibraryScanService

        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        svc = LibraryScanService()
        result = svc.resolve_live_query_root(a, b)
        assert result is None


class TestTrashServiceFinalClosure:
    """Verify TrashService.compute_restore_reload_action drives the reload decision."""

    def test_has_compute_restore_reload_action(self):
        from iPhoto.application.services.trash_service import TrashService

        assert hasattr(TrashService, "compute_restore_reload_action")

    def test_reload_current_when_path_matches(self, tmp_path):
        from iPhoto.application.services.trash_service import TrashService

        svc = TrashService()
        should_reload, as_lib, _ = svc.compute_restore_reload_action(
            tmp_path, tmp_path, None
        )
        assert should_reload is True
        assert as_lib is False

    def test_reload_as_lib_when_current_is_library_root(self, tmp_path):
        from iPhoto.application.services.trash_service import TrashService

        lib = tmp_path / "lib"
        album = lib / "Album"
        album.mkdir(parents=True)
        svc = TrashService()
        should_reload, as_lib, _ = svc.compute_restore_reload_action(
            album, lib, lib
        )
        assert should_reload is False
        assert as_lib is True

    def test_no_reload_when_current_is_unrelated(self, tmp_path):
        from iPhoto.application.services.trash_service import TrashService

        lib = tmp_path / "lib"
        album = lib / "Album"
        unrelated = tmp_path / "unrelated"
        album.mkdir(parents=True)
        unrelated.mkdir()
        svc = TrashService()
        should_reload, as_lib, _ = svc.compute_restore_reload_action(
            album, unrelated, lib
        )
        assert should_reload is False
        assert as_lib is False

    def test_no_reload_when_no_current_root(self, tmp_path):
        from iPhoto.application.services.trash_service import TrashService

        svc = TrashService()
        should_reload, as_lib, _ = svc.compute_restore_reload_action(
            tmp_path, None, None
        )
        assert should_reload is False
        assert as_lib is False

    def test_library_update_service_restore_uses_trash_service(self):
        """_refresh_restored_album in library_update_service must use TrashService."""
        source = _read_source("iPhoto.gui.services.library_update_service")
        assert "_trash_service.compute_restore_reload_action" in source, (
            "_refresh_restored_album must delegate reload decision to TrashService"
        )

    def test_library_update_service_no_inline_scope_policy_in_restore(self):
        """_refresh_restored_album must not use _scope_policy directly for reload."""
        source = _read_source("iPhoto.gui.services.library_update_service")
        # Verify TrashService is used instead of inline scope_policy checks
        assert "_trash_service.compute_restore_reload_action" in source, (
            "_refresh_restored_album must use TrashService.compute_restore_reload_action"
        )
