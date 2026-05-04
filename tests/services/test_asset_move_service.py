"""Unit tests for :mod:`iPhoto.gui.services.asset_move_service`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for asset move service tests",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtWidgets",
    reason="Qt widgets are required for asset move service tests",
    exc_type=ImportError,
)

from PySide6.QtWidgets import QApplication

import iPhoto.bootstrap.library_asset_lifecycle_service as lifecycle_module
from iPhoto.bootstrap.library_asset_operation_service import LibraryAssetOperationService
from iPhoto.cache.index_store import IndexStore
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.gui.services.asset_move_service import AssetMoveService
from iPhoto.gui.services.deletion_service import DeletionService
from iPhoto.gui.ui.tasks import move_worker as move_worker_module
from iPhoto.gui.ui.tasks.move_worker import MoveSignals, MoveWorker
from iPhoto.library.manager import LibraryManager


@pytest.fixture()
def qapp() -> QApplication:
    """Provide a QApplication instance shared across the module."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _create_service(
    *,
    task_manager,
    current_album,
    library_manager=None,
) -> AssetMoveService:
    """Convenience helper that instantiates the service under test."""

    return AssetMoveService(
        task_manager=task_manager,
        current_album_getter=current_album,
        library_manager_getter=(lambda: library_manager),
    )


class _LifecycleRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def apply_move(self, **kwargs):
        self.calls.append(kwargs)
        return lifecycle_module.AssetLifecycleResult()


def _build_operation_service(
    library_root: Path,
    *,
    lifecycle_service=None,
) -> LibraryAssetOperationService:
    return LibraryAssetOperationService(
        library_root,
        lifecycle_service=lifecycle_service,
    )


def test_move_assets_requires_active_album(
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """No album should result in an error and a rollback of optimistic moves."""

    task_manager = mocker.MagicMock()

    service = _create_service(
        task_manager=task_manager,
        current_album=lambda: None,
    )

    errors: list[str] = []
    service.errorRaised.connect(errors.append)

    accepted = service.move_assets([tmp_path / "file.jpg"], tmp_path / "dest")

    assert accepted is False
    assert errors == ["No album is currently open."]
    task_manager.submit_task.assert_not_called()


def test_move_assets_submits_worker_and_emits_completion(
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Valid requests should produce a background worker and emit results."""

    source_root = tmp_path / "Source"
    destination_root = tmp_path / "Destination"
    source_root.mkdir()
    destination_root.mkdir()
    asset = source_root / "photo.jpg"
    asset.write_bytes(b"data")

    task_manager = mocker.MagicMock()

    album = mocker.MagicMock()
    album.root = source_root
    lifecycle_service = _LifecycleRecorder()
    library_manager = mocker.MagicMock()
    library_manager.root.return_value = source_root
    library_manager.asset_operation_service = _build_operation_service(
        source_root,
        lifecycle_service=lifecycle_service,  # type: ignore[arg-type]
    )

    service = _create_service(
        task_manager=task_manager,
        current_album=lambda: album,
        library_manager=library_manager,
    )

    results: list[tuple[Path, Path, bool, str]] = []
    detailed_results: list[tuple] = []

    service.moveFinished.connect(
        lambda src, dest, success, message: results.append((src, dest, success, message))
    )
    # ``moveCompletedDetailed`` emits seven individual arguments.  Connecting
    # the signal directly to :py:meth:`list.append` would therefore raise a
    # ``TypeError`` because the built-in expects a single object.  Wrap the
    # parameters into a tuple so the test can capture the payload safely.
    service.moveCompletedDetailed.connect(
        lambda src, dest, pairs, src_ok, dest_ok, is_trash, is_restore: detailed_results.append(
            (src, dest, pairs, src_ok, dest_ok, is_trash, is_restore)
        )
    )

    accepted = service.move_assets([asset], destination_root)

    # The task manager should receive a worker submission with a unique identifier.
    assert accepted is True
    assert task_manager.submit_task.call_count == 1
    kwargs = task_manager.submit_task.call_args.kwargs
    assert kwargs["task_id"].startswith(
        f"move:move:{source_root}->{destination_root}:"
    )
    worker = kwargs["worker"]
    assert isinstance(worker, MoveWorker)

    # Simulate the completion callback triggered by the background manager.
    moved_pairs = [(asset, destination_root / asset.name)]
    kwargs["on_finished"](source_root, destination_root, moved_pairs, True, True)

    assert results == [(source_root, destination_root, True, "Moved 1 item.")]
    assert len(detailed_results) == 1
    src, dest, raw_pairs, source_ok, destination_ok, is_trash, is_restore = detailed_results[0]
    normalized_pairs = [(Path(src_path), Path(dest_path)) for src_path, dest_path in raw_pairs]

    assert src == source_root
    assert dest == destination_root
    assert normalized_pairs == [(asset, destination_root / asset.name)]
    assert source_ok is True
    assert destination_ok is True
    assert is_trash is False
    assert is_restore is False


def test_move_assets_falls_back_to_standalone_service_when_library_is_unbound(
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Standalone album moves should still queue a worker without a library session."""

    source_root = tmp_path / "Source"
    destination_root = tmp_path / "Destination"
    source_root.mkdir()
    destination_root.mkdir()
    asset = source_root / "photo.jpg"
    asset.write_bytes(b"data")

    task_manager = mocker.MagicMock()
    album = mocker.MagicMock()
    album.root = source_root
    service = _create_service(
        task_manager=task_manager,
        current_album=lambda: album,
        library_manager=None,
    )
    errors: list[str] = []
    service.errorRaised.connect(errors.append)

    accepted = service.move_assets([asset], destination_root)

    assert accepted is True
    assert task_manager.submit_task.call_count == 1
    worker = task_manager.submit_task.call_args.kwargs["worker"]
    assert isinstance(worker, MoveWorker)
    assert errors == []


def test_move_assets_uses_standalone_service_for_album_outside_bound_library(
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """A standalone album move should not write through the bound library service."""

    library_root = tmp_path / "Library"
    source_root = tmp_path / "StandaloneSource"
    destination_root = tmp_path / "StandaloneDestination"
    library_root.mkdir()
    source_root.mkdir()
    destination_root.mkdir()
    asset = source_root / "photo.jpg"
    asset.write_bytes(b"data")

    task_manager = mocker.MagicMock()
    album = mocker.MagicMock()
    album.root = source_root
    bound_lifecycle = _LifecycleRecorder()
    library_manager = mocker.MagicMock()
    library_manager.root.return_value = library_root
    library_manager.asset_operation_service = _build_operation_service(
        library_root,
        lifecycle_service=bound_lifecycle,  # type: ignore[arg-type]
    )

    service = _create_service(
        task_manager=task_manager,
        current_album=lambda: album,
        library_manager=library_manager,
    )

    accepted = service.move_assets([asset], destination_root)

    assert accepted is True
    worker = task_manager.submit_task.call_args.kwargs["worker"]
    assert worker._library_root is None
    assert worker.asset_lifecycle_service.library_root is None
    assert bound_lifecycle.calls == []


def test_delete_assets_skips_already_missing_sources(
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    library_root = tmp_path / "Library"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    trash_root.mkdir(parents=True)
    missing = library_root / "missing.jpg"

    task_manager = mocker.MagicMock()
    library_manager = mocker.MagicMock()
    library_manager.root.return_value = library_root
    library_manager.deleted_directory.return_value = trash_root
    library_manager.asset_operation_service = _build_operation_service(
        library_root,
        lifecycle_service=_LifecycleRecorder(),  # type: ignore[arg-type]
    )

    service = _create_service(
        task_manager=task_manager,
        current_album=lambda: None,
        library_manager=library_manager,
    )
    errors: list[str] = []
    results: list[tuple[Path, Path, bool, str]] = []
    service.errorRaised.connect(errors.append)
    service.moveFinished.connect(
        lambda src, dest, success, message: results.append((src, dest, success, message))
    )

    accepted = service.move_assets([missing], trash_root, operation="delete")

    assert accepted is False
    assert errors == []
    assert results == [(library_root, trash_root.resolve(), False, "No items were deleted.")]
    task_manager.submit_task.assert_not_called()


def test_delete_service_acceptance_runs_move_worker(
    mocker,
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The right-click delete chain should queue a worker that moves the file."""

    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    library_root.mkdir()
    album_root.mkdir(parents=True)
    asset = album_root / "photo.jpg"
    asset.write_bytes(b"data")

    library_manager = LibraryManager()
    library_manager.bind_path(library_root)
    trash_root = library_manager.ensure_deleted_directory()
    assert trash_root is not None

    def _fake_process_media_paths(root: Path, image_paths, video_paths):
        rows = []
        for candidate in list(image_paths) + list(video_paths):
            rows.append({"rel": candidate.resolve().relative_to(root).as_posix()})
        return rows

    lifecycle_service = lifecycle_module.LibraryAssetLifecycleService(
        library_root,
        media_processor=_fake_process_media_paths,
    )
    library_manager.bind_asset_lifecycle_service(lifecycle_service)
    library_manager.bind_asset_operation_service(
        LibraryAssetOperationService(
            library_root,
            lifecycle_service=lifecycle_service,
        )
    )
    monkeypatch.setattr(lifecycle_module.LibraryScanService, "pair_album", lambda *_: [])

    task_manager = mocker.MagicMock()
    album = mocker.MagicMock()
    album.root = album_root
    move_service = _create_service(
        task_manager=task_manager,
        current_album=lambda: album,
        library_manager=library_manager,
    )
    deletion_service = DeletionService(
        move_service=move_service,
        library_manager_getter=lambda: library_manager,
        model_provider_getter=lambda: None,
    )

    accepted = deletion_service.delete_assets([asset])

    assert accepted is True
    assert task_manager.submit_task.call_count == 1

    worker = task_manager.submit_task.call_args.kwargs["worker"]
    worker.run()

    trashed_asset = trash_root / asset.name
    assert not asset.exists()
    assert trashed_asset.exists()


def test_move_worker_moves_ipo_sidecar_with_media(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source_root = tmp_path / "Source"
    destination_root = tmp_path / "Destination"
    source_root.mkdir()
    destination_root.mkdir()
    asset = source_root / "photo.jpg"
    asset.write_bytes(b"image")
    edit_sidecar = source_root / "photo.ipo"
    edit_sidecar.write_text("<sidecar />")
    lifecycle = _LifecycleRecorder()

    worker = MoveWorker(
        [asset],
        source_root,
        destination_root,
        MoveSignals(),
        asset_lifecycle_service=lifecycle,  # type: ignore[arg-type]
    )

    worker.run()

    moved_asset = destination_root / "photo.jpg"
    moved_sidecar = destination_root / "photo.ipo"
    assert moved_asset.exists()
    assert moved_sidecar.read_text() == "<sidecar />"
    assert not asset.exists()
    assert not edit_sidecar.exists()
    assert lifecycle.calls[0]["moved"] == [(asset.resolve(), moved_asset.resolve())]


def test_move_worker_avoids_existing_ipo_collision(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source_root = tmp_path / "Source"
    destination_root = tmp_path / "Destination"
    source_root.mkdir()
    destination_root.mkdir()
    asset = source_root / "photo.jpg"
    asset.write_bytes(b"image")
    edit_sidecar = source_root / "photo.ipo"
    edit_sidecar.write_text("source edits")
    existing_sidecar = destination_root / "photo.ipo"
    existing_sidecar.write_text("existing edits")

    worker = MoveWorker(
        [asset],
        source_root,
        destination_root,
        MoveSignals(),
        asset_lifecycle_service=_LifecycleRecorder(),  # type: ignore[arg-type]
    )

    worker.run()

    assert not (destination_root / "photo.jpg").exists()
    assert (destination_root / "photo (1).jpg").exists()
    assert (destination_root / "photo (1).ipo").read_text() == "source edits"
    assert existing_sidecar.read_text() == "existing edits"


def test_move_worker_keeps_live_photo_stem_when_sidecar_is_shared(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source_root = tmp_path / "Source"
    destination_root = tmp_path / "Destination"
    source_root.mkdir()
    destination_root.mkdir()
    still = source_root / "IMG_3686.HEIC"
    motion = source_root / "IMG_3686.MOV"
    still.write_bytes(b"still")
    motion.write_bytes(b"motion")
    edit_sidecar = source_root / "IMG_3686.ipo"
    edit_sidecar.write_text("shared edits")
    lifecycle = _LifecycleRecorder()

    worker = MoveWorker(
        [still, motion],
        source_root,
        destination_root,
        MoveSignals(),
        asset_lifecycle_service=lifecycle,  # type: ignore[arg-type]
    )

    worker.run()

    moved_still = destination_root / "IMG_3686.HEIC"
    moved_motion = destination_root / "IMG_3686.MOV"
    assert moved_still.exists()
    assert moved_motion.exists()
    assert (destination_root / "IMG_3686.ipo").read_text() == "shared edits"
    assert not (destination_root / "IMG_3686 (1).MOV").exists()
    assert lifecycle.calls[0]["moved"] == [
        (still.resolve(), moved_still.resolve()),
        (motion.resolve(), moved_motion.resolve()),
    ]


def test_move_assets_passes_session_lifecycle_service(
    mocker,
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Move workers should receive the active session lifecycle command surface."""

    library_root = tmp_path / "Library"
    source_root = library_root / "Source"
    destination_root = library_root / "Destination"
    source_root.mkdir(parents=True)
    destination_root.mkdir()
    asset = source_root / "photo.jpg"
    asset.write_bytes(b"data")
    lifecycle_service = _LifecycleRecorder()

    task_manager = mocker.MagicMock()
    album = mocker.MagicMock()
    album.root = source_root
    library_manager = mocker.MagicMock()
    library_manager.root.return_value = library_root
    library_manager.asset_operation_service = _build_operation_service(
        library_root,
        lifecycle_service=lifecycle_service,  # type: ignore[arg-type]
    )

    service = _create_service(
        task_manager=task_manager,
        current_album=lambda: album,
        library_manager=library_manager,
    )

    accepted = service.move_assets([asset], destination_root)

    assert accepted is True
    worker = task_manager.submit_task.call_args.kwargs["worker"]
    assert worker.asset_lifecycle_service is lifecycle_service


def test_restore_repopulates_library_index(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restoring from trash should reinsert rows into the library-wide index."""

    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"

    library_root.mkdir()
    album_root.mkdir(parents=True)

    library_manager = LibraryManager()
    library_manager.bind_path(library_root)
    resolved_trash = library_manager.ensure_deleted_directory()
    assert resolved_trash is not None
    trash_root = resolved_trash

    asset_name = "IMG_0001.JPG"
    trashed_asset = trash_root / asset_name
    trashed_asset.write_bytes(b"stub")

    def _fake_process_media_paths(root: Path, image_paths, video_paths):
        """Return minimal index rows keyed by their relative path."""

        rows = []
        for candidate in list(image_paths) + list(video_paths):
            rows.append({"rel": candidate.resolve().relative_to(root).as_posix()})
        return rows

    monkeypatch.setattr(lifecycle_module, "process_media_paths", _fake_process_media_paths)
    monkeypatch.setattr(lifecycle_module.LibraryScanService, "pair_album", lambda *_: [])

    restore_signals = MoveSignals()
    worker = MoveWorker(
        [trashed_asset],
        trash_root,
        album_root,
        restore_signals,
        library_root=library_root,
        trash_root=trash_root,
        is_restore=True,
        asset_lifecycle_service=lifecycle_module.LibraryAssetLifecycleService(
            library_root
        ),
    )

    worker.run()

    restored_asset = album_root / asset_name
    assert restored_asset.exists()
    assert not trashed_asset.exists()

    # MoveWorker stores everything in the global library-root DB.
    library_rows = list(IndexStore(library_root).read_all())
    assert any(row.get("rel") == f"AlbumA/{asset_name}" for row in library_rows)


def test_restore_moves_ipo_sidecar_with_renamed_media(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    library_root.mkdir()
    album_root.mkdir(parents=True)

    library_manager = LibraryManager()
    library_manager.bind_path(library_root)
    trash_root = library_manager.ensure_deleted_directory()
    assert trash_root is not None

    asset_name = "IMG_0003.JPG"
    trashed_asset = trash_root / asset_name
    trashed_asset.write_bytes(b"trash")
    trashed_sidecar = trash_root / "IMG_0003.ipo"
    trashed_sidecar.write_text("restored edits")
    existing_asset = album_root / asset_name
    existing_asset.write_bytes(b"existing")

    def _fake_process_media_paths(root: Path, image_paths, video_paths):
        rows = []
        for candidate in list(image_paths) + list(video_paths):
            rel = candidate.resolve().relative_to(root).as_posix()
            rows.append({"rel": rel})
        return rows

    monkeypatch.setattr(lifecycle_module, "process_media_paths", _fake_process_media_paths)
    monkeypatch.setattr(lifecycle_module.LibraryScanService, "pair_album", lambda *_: [])

    worker = MoveWorker(
        [trashed_asset],
        trash_root,
        album_root,
        MoveSignals(),
        library_root=library_root,
        trash_root=trash_root,
        is_restore=True,
        asset_lifecycle_service=lifecycle_module.LibraryAssetLifecycleService(
            library_root
        ),
    )

    worker.run()

    restored_asset = album_root / "IMG_0003 (1).JPG"
    restored_sidecar = album_root / "IMG_0003 (1).ipo"
    assert restored_asset.exists()
    assert restored_sidecar.read_text() == "restored edits"
    assert existing_asset.exists()
    assert not trashed_asset.exists()
    assert not trashed_sidecar.exists()
    rows = list(IndexStore(library_root).read_all())
    assert any(row.get("rel") == "AlbumA/IMG_0003 (1).JPG" for row in rows)
    assert not any(str(row.get("rel", "")).endswith(".ipo") for row in rows)


def test_delete_records_original_path_for_restore(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moving into trash should annotate rows with the original relative path."""

    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    library_root.mkdir()
    album_root.mkdir(parents=True)

    asset_name = "IMG_0002.JPG"
    asset = album_root / asset_name
    asset.write_bytes(b"stub")
    edit_sidecar = album_root / "IMG_0002.ipo"
    edit_sidecar.write_text("edits")

    library_manager = LibraryManager()
    library_manager.bind_path(library_root)
    trash_root = library_manager.ensure_deleted_directory()
    assert trash_root is not None

    def _fake_process_media_paths(root: Path, image_paths, video_paths):
        rows = []
        for candidate in list(image_paths) + list(video_paths):
            rows.append({"rel": candidate.resolve().relative_to(root).as_posix()})
        return rows

    monkeypatch.setattr(lifecycle_module, "process_media_paths", _fake_process_media_paths)
    monkeypatch.setattr(lifecycle_module.LibraryScanService, "pair_album", lambda *_: [])

    delete_signals = MoveSignals()
    worker = MoveWorker(
        [asset],
        album_root,
        trash_root,
        delete_signals,
        library_root=library_root,
        trash_root=trash_root,
        asset_lifecycle_service=lifecycle_module.LibraryAssetLifecycleService(
            library_root
        ),
    )
    worker.run()

    rows = list(IndexStore(library_root).read_all())
    trash_rel = (Path(RECENTLY_DELETED_DIR_NAME) / asset_name).as_posix()
    matching = [row for row in rows if row.get("rel") == trash_rel]
    assert len(matching) == 1, "Trashed asset row should be present in the index"
    assert matching[0].get("original_rel_path") == asset.relative_to(library_root).as_posix()
    assert (trash_root / "IMG_0002.ipo").read_text() == "edits"
    assert not edit_sidecar.exists()
    assert not any(str(row.get("rel", "")).endswith(".ipo") for row in rows)


def test_move_from_library_root_updates_source_album_index(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moving from the library view must trim the concrete source album index."""

    library_root = tmp_path / "Library"
    album_a = library_root / "AlbumA"
    album_b = library_root / "AlbumB"
    library_root.mkdir()
    album_a.mkdir(parents=True)
    album_b.mkdir(parents=True)

    asset = album_a / "IMG_0100.JPG"
    asset.write_bytes(b"asset")

    def _fake_process_media_paths(root: Path, image_paths, video_paths):
        rows = []
        for candidate in list(image_paths) + list(video_paths):
            rel = candidate.resolve().relative_to(root).as_posix()
            rows.append({"rel": rel})
        return rows

    monkeypatch.setattr(lifecycle_module, "process_media_paths", _fake_process_media_paths)
    monkeypatch.setattr(lifecycle_module.LibraryScanService, "pair_album", lambda *_: [])

    # Pre-populate the global library-root DB (MoveWorker only uses this DB).
    IndexStore(library_root).write_rows(
        [{"rel": f"AlbumA/{asset.name}", "abs": str(asset.resolve())}]
    )

    signals = MoveSignals()
    worker = MoveWorker(
        [asset],
        library_root,
        album_b,
        signals,
        library_root=library_root,
        asset_lifecycle_service=lifecycle_module.LibraryAssetLifecycleService(
            library_root
        ),
    )

    worker.run()

    # The MoveWorker only uses the single global DB at library_root.
    library_rows = list(IndexStore(library_root).read_all())
    # Source row removed, destination row inserted.
    assert not any(r.get("rel") == f"AlbumA/{asset.name}" for r in library_rows)
    assert any(r.get("rel") == f"AlbumB/{asset.name}" for r in library_rows)


def test_delete_collision_assigns_unique_trash_paths(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting same-named files from different albums should keep unique trash rels."""

    library_root = tmp_path / "Library"
    album_a = library_root / "AlbumA"
    album_b = library_root / "AlbumB"
    library_root.mkdir()
    album_a.mkdir(parents=True)
    album_b.mkdir(parents=True)

    asset_name = "IMG_1000.JPG"
    asset_a = album_a / asset_name
    asset_b = album_b / asset_name
    asset_a.write_bytes(b"a")
    asset_b.write_bytes(b"b")

    library_manager = LibraryManager()
    library_manager.bind_path(library_root)
    trash_root = library_manager.ensure_deleted_directory()
    assert trash_root is not None

    def _fake_process_media_paths(root: Path, image_paths, video_paths):
        rows = []
        for candidate in list(image_paths) + list(video_paths):
            rel = candidate.resolve().relative_to(root).as_posix()
            rows.append({"rel": rel, "ts": 1})
        return rows

    monkeypatch.setattr(lifecycle_module, "process_media_paths", _fake_process_media_paths)
    monkeypatch.setattr(lifecycle_module.LibraryScanService, "pair_album", lambda *_: [])

    # First delete
    signals = MoveSignals()
    worker = MoveWorker(
        [asset_a],
        album_a,
        trash_root,
        signals,
        library_root=library_root,
        trash_root=trash_root,
        asset_lifecycle_service=lifecycle_module.LibraryAssetLifecycleService(
            library_root
        ),
    )
    worker.run()

    # Second delete with same name from different album
    signals2 = MoveSignals()
    worker2 = MoveWorker(
        [asset_b],
        album_b,
        trash_root,
        signals2,
        library_root=library_root,
        trash_root=trash_root,
        asset_lifecycle_service=lifecycle_module.LibraryAssetLifecycleService(
            library_root
        ),
    )
    worker2.run()

    rows = list(IndexStore(library_root).read_all())
    trash_rows = [
        row for row in rows if row.get("rel", "").startswith(f"{RECENTLY_DELETED_DIR_NAME}/")
    ]
    assert len(trash_rows) == 2
    rel_values = {row.get("rel") for row in trash_rows}
    assert len(rel_values) == 2
    # Ensure filesystem paths exist for both rels
    for rel in rel_values:
        assert (library_root / rel).exists()


def test_move_worker_cleans_up_partially_copied_target_on_move_failure(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "Source"
    destination_root = tmp_path / "RecentlyDeleted"
    source_root.mkdir()
    destination_root.mkdir()

    source = source_root / "locked.mp4"
    source.write_bytes(b"video")

    def _copy_then_fail(src: str, dest: str) -> str:
        Path(dest).write_bytes(Path(src).read_bytes())
        raise PermissionError(32, "The process cannot access the file because it is being used by another process", src)

    monkeypatch.setattr(move_worker_module.shutil, "move", _copy_then_fail)

    worker = MoveWorker(
        [source],
        source_root,
        destination_root,
        MoveSignals(),
    )

    with pytest.raises(PermissionError):
        worker._move_into_destination(source)

    assert source.exists()
    assert not (destination_root / source.name).exists()


def test_move_worker_does_not_move_media_when_sidecar_move_fails(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "Source"
    destination_root = tmp_path / "Destination"
    source_root.mkdir()
    destination_root.mkdir()
    asset = source_root / "photo.jpg"
    asset.write_bytes(b"image")
    edit_sidecar = source_root / "photo.ipo"
    edit_sidecar.write_text("edits")
    lifecycle = _LifecycleRecorder()

    def _fail_sidecar_move(src: str, _dest: str) -> str:
        source_path = Path(src)
        if source_path.suffix.lower() == ".ipo":
            raise PermissionError(32, "locked", src)
        raise AssertionError("media should not move after sidecar failure")

    monkeypatch.setattr(move_worker_module.shutil, "move", _fail_sidecar_move)
    signals = MoveSignals()
    errors: list[str] = []
    finished: list[tuple[Path, Path, list, bool, bool]] = []
    signals.error.connect(errors.append)
    signals.finished.connect(
        lambda src, dest, moved, source_ok, destination_ok: finished.append(
            (src, dest, moved, source_ok, destination_ok)
        )
    )
    worker = MoveWorker(
        [asset],
        source_root,
        destination_root,
        signals,
        asset_lifecycle_service=lifecycle,  # type: ignore[arg-type]
    )

    worker.run()

    assert asset.exists()
    assert edit_sidecar.exists()
    assert not (destination_root / "photo.jpg").exists()
    assert not (destination_root / "photo.ipo").exists()
    assert lifecycle.calls == []
    assert finished == [(source_root, destination_root, [], True, True)]
    assert len(errors) == 1
    assert "Could not move" in errors[0]


def test_move_worker_rolls_back_sidecar_when_media_move_fails(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "Source"
    destination_root = tmp_path / "Destination"
    source_root.mkdir()
    destination_root.mkdir()
    asset = source_root / "photo.jpg"
    asset.write_bytes(b"image")
    edit_sidecar = source_root / "photo.ipo"
    edit_sidecar.write_text("edits")
    lifecycle = _LifecycleRecorder()
    original_move = move_worker_module.shutil.move

    def _move_sidecar_then_fail_media(src: str, dest: str) -> str:
        source_path = Path(src)
        if source_path.suffix.lower() == ".ipo":
            return original_move(src, dest)
        raise PermissionError(32, "locked", src)

    monkeypatch.setattr(move_worker_module.shutil, "move", _move_sidecar_then_fail_media)
    signals = MoveSignals()
    errors: list[str] = []
    signals.error.connect(errors.append)
    worker = MoveWorker(
        [asset],
        source_root,
        destination_root,
        signals,
        asset_lifecycle_service=lifecycle,  # type: ignore[arg-type]
    )

    worker.run()

    assert asset.exists()
    assert edit_sidecar.read_text() == "edits"
    assert not (destination_root / "photo.jpg").exists()
    assert not (destination_root / "photo.ipo").exists()
    assert lifecycle.calls == []
    assert len(errors) == 1
    assert "Could not move" in errors[0]


def test_delete_worker_skips_already_missing_sources(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    source_root = tmp_path / "Library"
    trash_root = source_root / RECENTLY_DELETED_DIR_NAME
    trash_root.mkdir(parents=True)
    missing = source_root / "missing.jpg"

    signals = MoveSignals()
    errors: list[str] = []
    finished: list[tuple[Path, Path, list, bool, bool]] = []
    signals.error.connect(errors.append)
    signals.finished.connect(
        lambda src, dest, moved, source_ok, destination_ok: finished.append(
            (src, dest, moved, source_ok, destination_ok)
        )
    )
    worker = MoveWorker(
        [missing],
        source_root,
        trash_root,
        signals,
        library_root=source_root,
        trash_root=trash_root,
    )

    worker.run()

    assert errors == []
    assert finished == [(source_root, trash_root, [], True, True)]
