from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for restoration service tests",
    exc_type=ImportError,
)

from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.gui.services.restoration_service import RestorationService


class _FakeLifecycleService:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.rows_by_rel: dict[str, dict] = {}
        self.read_roots: list[Path] = []
        self.read_rels: list[list[str]] = []

    def read_restore_index_rows(self, trash_root: Path) -> list[dict]:
        self.read_roots.append(Path(trash_root))
        return list(self.rows)

    def read_index_rows_by_rels(self, rels) -> dict[str, dict]:
        materialized = list(rels)
        self.read_rels.append(materialized)
        return {
            rel: dict(self.rows_by_rel[rel])
            for rel in materialized
            if rel in self.rows_by_rel
        }


class _FakeLibrary:
    def __init__(
        self,
        root: Path,
        lifecycle_service: _FakeLifecycleService,
        *,
        albums_by_uuid: dict[str, object] | None = None,
    ) -> None:
        self._root = Path(root)
        self.asset_lifecycle_service = lifecycle_service
        self._albums_by_uuid = albums_by_uuid or {}

    def root(self) -> Path:
        return self._root

    def deleted_directory(self) -> Path:
        return self._root / RECENTLY_DELETED_DIR_NAME

    def find_album_by_uuid(self, _album_id: str):
        return self._albums_by_uuid.get(_album_id)


class _FakeAlbumNode:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)


class _FakeMoveService:
    def __init__(self) -> None:
        self.calls: list[tuple[list[Path], Path, str]] = []

    def move_assets(
        self,
        paths,
        destination_root: Path,
        *,
        operation: str = "move",
    ) -> bool:
        self.calls.append((list(paths), Path(destination_root), operation))
        return True


class _FakeModel:
    def __init__(self, metadata_by_path: dict[Path, dict]) -> None:
        self._metadata_by_path = {
            Path(path).resolve(): dict(metadata)
            for path, metadata in metadata_by_path.items()
        }

    def metadata_for_path(self, path: Path):
        return self._metadata_by_path.get(Path(path).resolve())


def test_restore_uses_lifecycle_rows_to_resolve_destination(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    album_root.mkdir(parents=True)
    (album_root / ".iphoto.album.json").write_text(
        '{"id": "album-a"}',
        encoding="utf-8",
    )
    trash_root.mkdir()
    trashed_asset = trash_root / "photo.jpg"
    trashed_asset.write_bytes(b"data")

    lifecycle = _FakeLifecycleService(
        [
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg",
                "original_rel_path": "AlbumA/photo.jpg",
            }
        ]
    )
    library = _FakeLibrary(library_root, lifecycle)
    move_service = _FakeMoveService()
    service = RestorationService(
        move_service=move_service,  # type: ignore[arg-type]
        library_manager_getter=lambda: library,  # type: ignore[return-value]
        model_provider_getter=lambda: None,
        restore_prompt_getter=lambda: None,
    )

    scheduled = service.restore_assets([trashed_asset])

    assert scheduled is True
    assert lifecycle.read_roots == [trash_root]
    assert move_service.calls == [([trashed_asset], album_root, "restore")]


def test_restore_uses_stale_original_rows_when_trash_rows_are_missing(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    trash_root.mkdir(parents=True)
    trashed_asset = trash_root / "photo.jpg"
    trashed_asset.write_bytes(b"data")

    lifecycle = _FakeLifecycleService([])
    lifecycle.rows_by_rel = {
        "photo.jpg": {
            "rel": "photo.jpg",
        }
    }
    library = _FakeLibrary(library_root, lifecycle)
    move_service = _FakeMoveService()
    service = RestorationService(
        move_service=move_service,  # type: ignore[arg-type]
        library_manager_getter=lambda: library,  # type: ignore[return-value]
        model_provider_getter=lambda: None,
        restore_prompt_getter=lambda: None,
    )

    scheduled = service.restore_assets([trashed_asset])

    assert scheduled is True
    assert lifecycle.read_rels == [["photo.jpg"]]
    assert move_service.calls == [([trashed_asset], library_root, "restore")]


def test_restore_uses_album_metadata_to_recover_stale_subalbum_rows(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    album_root.mkdir(parents=True)
    (album_root / ".iphoto.album.json").write_text(
        '{"id": "album-a"}',
        encoding="utf-8",
    )
    trash_root.mkdir()
    trashed_asset = trash_root / "photo.jpg"
    trashed_asset.write_bytes(b"data")

    lifecycle = _FakeLifecycleService(
        [
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg",
                "original_album_id": "album-a",
                "original_album_subpath": "photo.jpg",
            }
        ]
    )
    lifecycle.rows_by_rel = {
        "AlbumA/photo.jpg": {
            "rel": "AlbumA/photo.jpg",
            "original_rel_path": "AlbumA/photo.jpg",
        }
    }
    library = _FakeLibrary(
        library_root,
        lifecycle,
        albums_by_uuid={"album-a": _FakeAlbumNode(album_root)},
    )
    move_service = _FakeMoveService()
    service = RestorationService(
        move_service=move_service,  # type: ignore[arg-type]
        library_manager_getter=lambda: library,  # type: ignore[return-value]
        model_provider_getter=lambda: None,
        restore_prompt_getter=lambda: None,
    )

    scheduled = service.restore_assets([trashed_asset])

    assert scheduled is True
    assert lifecycle.read_rels == [["AlbumA/photo.jpg", "photo.jpg"]]
    assert move_service.calls == [([trashed_asset], album_root, "restore")]


def test_restore_live_photo_adds_same_stem_motion_without_model_metadata(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    trash_root.mkdir(parents=True)
    still = trash_root / "IMG_3686.HEIC"
    motion = trash_root / "IMG_3686.MOV"
    still.write_bytes(b"still")
    motion.write_bytes(b"motion")

    lifecycle = _FakeLifecycleService([])
    lifecycle.rows_by_rel = {
        "IMG_3686.HEIC": {"rel": "IMG_3686.HEIC"},
        "IMG_3686.MOV": {"rel": "IMG_3686.MOV"},
    }
    library = _FakeLibrary(library_root, lifecycle)
    move_service = _FakeMoveService()
    service = RestorationService(
        move_service=move_service,  # type: ignore[arg-type]
        library_manager_getter=lambda: library,  # type: ignore[return-value]
        model_provider_getter=lambda: None,
        restore_prompt_getter=lambda: None,
    )

    scheduled = service.restore_assets([still])

    assert scheduled is True
    assert move_service.calls == [
        ([still.resolve(), motion.resolve()], library_root, "restore")
    ]


def test_restore_non_live_photo_does_not_add_same_stem_motion(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    album_root.mkdir(parents=True)
    trash_root.mkdir()
    still = trash_root / "IMG_0001.JPG"
    motion = trash_root / "IMG_0001.MOV"
    still.write_bytes(b"still")
    motion.write_bytes(b"motion")

    lifecycle = _FakeLifecycleService(
        [
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/IMG_0001.JPG",
                "original_rel_path": "AlbumA/IMG_0001.JPG",
            },
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/IMG_0001.MOV",
                "original_rel_path": "AlbumA/IMG_0001.MOV",
            },
        ]
    )
    library = _FakeLibrary(library_root, lifecycle)
    move_service = _FakeMoveService()
    model = _FakeModel({still: {"is_live": False}})
    service = RestorationService(
        move_service=move_service,  # type: ignore[arg-type]
        library_manager_getter=lambda: library,  # type: ignore[return-value]
        model_provider_getter=lambda: lambda: model,
        restore_prompt_getter=lambda: None,
    )

    scheduled = service.restore_assets([still])

    assert scheduled is True
    assert move_service.calls == [([still.resolve()], album_root, "restore")]
