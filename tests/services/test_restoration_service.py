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
        self.read_roots: list[Path] = []

    def read_restore_index_rows(self, trash_root: Path) -> list[dict]:
        self.read_roots.append(Path(trash_root))
        return list(self.rows)


class _FakeLibrary:
    def __init__(self, root: Path, lifecycle_service: _FakeLifecycleService) -> None:
        self._root = Path(root)
        self.asset_lifecycle_service = lifecycle_service

    def root(self) -> Path:
        return self._root

    def deleted_directory(self) -> Path:
        return self._root / RECENTLY_DELETED_DIR_NAME

    def find_album_by_uuid(self, _album_id: str):
        return None


class _FakeMoveService:
    def __init__(self) -> None:
        self.calls: list[tuple[list[Path], Path, str]] = []

    def move_assets(
        self,
        paths,
        destination_root: Path,
        *,
        operation: str = "move",
    ) -> None:
        self.calls.append((list(paths), Path(destination_root), operation))


def test_restore_uses_lifecycle_rows_to_resolve_destination(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    album_root.mkdir(parents=True)
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
