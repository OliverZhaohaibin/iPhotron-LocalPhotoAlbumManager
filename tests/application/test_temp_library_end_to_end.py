from __future__ import annotations

import os
import shutil
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for temp-library worker regressions",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtCore",
    reason="QtCore is required for temp-library worker regressions",
    exc_type=ImportError,
)

from PySide6.QtCore import QCoreApplication

import iPhoto.bootstrap.library_scan_service as scan_service_module
from iPhoto.bootstrap.library_asset_lifecycle_service import (
    LibraryAssetLifecycleService,
)
from iPhoto.bootstrap.library_asset_operation_service import (
    LibraryAssetOperationService,
)
from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.bootstrap.library_session import LibrarySession
from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.gui.ui.tasks.import_worker import ImportSignals, ImportWorker
from iPhoto.gui.ui.tasks.move_worker import MoveSignals, MoveWorker
from iPhoto.media_classifier import ALL_IMAGE_EXTENSIONS, VIDEO_EXTENSIONS


class _FakeAssetRuntime:
    def __init__(self) -> None:
        self.bound_roots: list[Path] = []
        self.bound_edit_services: list[object | None] = []

    def bind_library_root(self, root: Path) -> None:
        self.bound_roots.append(Path(root))

    def bind_edit_service(self, edit_service: object | None) -> None:
        self.bound_edit_services.append(edit_service)

    def shutdown(self) -> None:
        return None


class _NullMapRuntime:
    def capabilities(self):
        return None


class _NullMapInteractionService:
    def activate_marker_assets(self, _assets):
        return None


class _FilesystemScanner:
    def scan(
        self,
        root: Path,
        _include: Iterable[str],
        _exclude: Iterable[str],
        **_kwargs: object,
    ) -> Iterator[dict[str, Any]]:
        for asset_path in _iter_media_files(Path(root)):
            yield _build_row(Path(root), asset_path)


def _iter_media_files(root: Path) -> Iterator[Path]:
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        if ".iPhoto" in candidate.parts or ".iphoto" in candidate.parts:
            continue
        suffix = candidate.suffix.lower()
        if suffix in ALL_IMAGE_EXTENSIONS or suffix in VIDEO_EXTENSIONS:
            yield candidate


def _build_row(root: Path, asset_path: Path) -> dict[str, Any]:
    rel = asset_path.resolve().relative_to(root.resolve()).as_posix()
    suffix = asset_path.suffix.lower()
    is_video = suffix in VIDEO_EXTENSIONS
    return {
        "rel": rel,
        "id": f"asset:{rel}",
        "dt": "2024-01-01T00:00:00Z",
        "ts": 1704067200,
        "bytes": asset_path.stat().st_size,
        "mime": "video/quicktime" if is_video else "image/jpeg",
        "media_type": 2 if is_video else 1,
    }


def _fake_process_media_paths(
    root: Path,
    image_paths: list[Path],
    video_paths: list[Path],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for asset_path in list(image_paths) + list(video_paths):
        rows.append(_build_row(Path(root), Path(asset_path)))
    return rows


def _copy_into_album(source: Path, destination_root: Path) -> Path:
    destination = Path(destination_root) / Path(source).name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def _make_session(library_root: Path) -> LibrarySession:
    scan_service = LibraryScanService(
        library_root,
        scanner=_FilesystemScanner(),
    )
    lifecycle_service = LibraryAssetLifecycleService(
        library_root,
        scan_service=scan_service,
        media_processor=_fake_process_media_paths,
    )
    operation_service = LibraryAssetOperationService(
        library_root,
        lifecycle_service=lifecycle_service,
    )
    return LibrarySession(
        library_root,
        asset_runtime=_FakeAssetRuntime(),
        scans=scan_service,
        asset_lifecycle=lifecycle_service,
        asset_operations=operation_service,
        people=object(),  # type: ignore[arg-type]
        maps=_NullMapRuntime(),  # type: ignore[arg-type]
        map_interactions=_NullMapInteractionService(),  # type: ignore[arg-type]
    )


def _run_move_plan(plan, *, is_restore: bool = False) -> None:
    assert plan.accepted is True
    worker = MoveWorker(
        plan.sources,
        plan.source_root,
        plan.destination_root,
        MoveSignals(),
        library_root=plan.library_root,
        trash_root=plan.trash_root,
        is_restore=is_restore,
        asset_lifecycle_service=plan.asset_lifecycle_service,
    )
    worker.run()


@pytest.fixture(autouse=True)
def _reset_global_index() -> None:
    reset_global_repository()
    yield
    reset_global_repository()


@pytest.fixture()
def qapp() -> QCoreApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def test_import_worker_updates_temp_library_session_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    qapp: QCoreApplication,
) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    source_root = tmp_path / "Incoming"
    album_root.mkdir(parents=True)
    source_root.mkdir()
    source = source_root / "imported.jpg"
    source.write_bytes(b"import-me")
    session = _make_session(library_root)

    monkeypatch.setattr(
        scan_service_module,
        "process_media_paths",
        _fake_process_media_paths,
    )

    signals = ImportSignals()
    finished: list[tuple[Path, list[Path], bool]] = []
    errors: list[str] = []
    signals.finished.connect(
        lambda root, imported, success: finished.append((root, list(imported), success))
    )
    signals.error.connect(errors.append)

    worker = ImportWorker(
        [source],
        album_root,
        _copy_into_album,
        signals,
        scan_service=session.scans,
        asset_lifecycle_service=session.asset_lifecycle,
    )
    worker.run()

    indexed = {
        row["rel"]: row
        for row in get_global_repository(library_root).read_all(filter_hidden=False)
    }
    imported_path = album_root / source.name
    assert errors == []
    assert finished == [(album_root, [imported_path], True)]
    assert imported_path.exists()
    assert set(indexed) == {"AlbumA/imported.jpg"}


def test_move_worker_moves_between_albums_inside_temp_library(
    tmp_path: Path,
    qapp: QCoreApplication,
) -> None:
    library_root = tmp_path / "Library"
    album_a = library_root / "AlbumA"
    album_b = library_root / "AlbumB"
    album_a.mkdir(parents=True)
    album_b.mkdir()
    asset = album_a / "photo.jpg"
    asset.write_bytes(b"move-me")
    session = _make_session(library_root)
    session.scans.rescan_album(library_root)

    plan = session.asset_operations.plan_move_request(
        [asset],
        album_b,
        current_album_root=album_a,
    )
    _run_move_plan(plan)

    indexed = {
        row["rel"]: row
        for row in get_global_repository(library_root).read_all(filter_hidden=False)
    }
    assert not asset.exists()
    assert (album_b / "photo.jpg").exists()
    assert set(indexed) == {"AlbumB/photo.jpg"}


def test_delete_and_restore_preserve_trash_metadata_in_temp_library(
    tmp_path: Path,
    qapp: QCoreApplication,
) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    album_root.mkdir(parents=True)
    (album_root / ".iphoto.album.json").write_text(
        '{"id": "album-a"}',
        encoding="utf-8",
    )
    asset = album_root / "photo.jpg"
    asset.write_bytes(b"delete-and-restore")
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    trash_root.mkdir()
    session = _make_session(library_root)
    session.scans.rescan_album(library_root)

    delete_plan = session.asset_operations.plan_delete_request(
        [asset],
        trash_root=trash_root,
    )
    _run_move_plan(delete_plan)

    trashed = trash_root / "photo.jpg"
    indexed_after_delete = {
        row["rel"]: row
        for row in get_global_repository(library_root).read_all(filter_hidden=False)
    }
    trashed_row = indexed_after_delete[f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg"]
    assert not asset.exists()
    assert trashed.exists()
    assert trashed_row["original_rel_path"] == "AlbumA/photo.jpg"
    assert isinstance(trashed_row["original_album_id"], str)
    assert trashed_row["original_album_id"]
    assert str(trashed_row["original_album_subpath"]).endswith("photo.jpg")

    restore_plan = session.asset_operations.plan_restore_request(
        [trashed],
        trash_root=trash_root,
    )
    assert restore_plan.errors == []
    assert len(restore_plan.batches) == 1
    _run_move_plan(restore_plan.batches[0], is_restore=True)

    indexed_after_restore = {
        row["rel"]: row
        for row in get_global_repository(library_root).read_all(filter_hidden=False)
    }
    restored_row = indexed_after_restore["AlbumA/photo.jpg"]
    assert (album_root / "photo.jpg").exists()
    assert not trashed.exists()
    assert restored_row.get("original_rel_path") is None
    assert restored_row.get("original_album_id") is None
    assert restored_row.get("original_album_subpath") is None


def test_favorite_survives_rescan_in_temp_library(
    tmp_path: Path,
    qapp: QCoreApplication,
) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    album_root.mkdir(parents=True)
    asset = album_root / "favorite.jpg"
    asset.write_bytes(b"favorite")
    session = _make_session(library_root)
    session.scans.rescan_album(library_root)

    session.state.set_favorite_status("AlbumA/favorite.jpg", True)
    session.scans.rescan_album(library_root)

    row = get_global_repository(library_root).get_rows_by_rels(
        ["AlbumA/favorite.jpg"]
    )["AlbumA/favorite.jpg"]
    assert bool(row["is_favorite"]) is True
