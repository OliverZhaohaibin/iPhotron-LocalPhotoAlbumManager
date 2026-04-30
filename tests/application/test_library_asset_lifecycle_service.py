from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from iPhoto.bootstrap.library_asset_lifecycle_service import (
    LibraryAssetLifecycleService,
)
from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.config import RECENTLY_DELETED_DIR_NAME


class _PairRecorder:
    def __init__(self) -> None:
        self.pair_roots: list[Path] = []

    def pair_album(self, root: Path) -> list[object]:
        self.pair_roots.append(Path(root))
        return []


def _rows(root: Path) -> dict[str, dict[str, Any]]:
    return {
        str(row["rel"]): row
        for row in get_global_repository(root).read_all(filter_hidden=False)
    }


@pytest.fixture(autouse=True)
def clean_global_repository():
    reset_global_repository()
    yield
    reset_global_repository()


def test_apply_move_reuses_cached_metadata_and_pairs_once(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_a = library_root / "AlbumA"
    album_b = library_root / "AlbumB"
    album_a.mkdir(parents=True)
    album_b.mkdir()
    source = album_a / "photo.jpg"
    target = album_b / "photo.jpg"
    source.write_bytes(b"source")
    target.write_bytes(b"target")

    get_global_repository(library_root).write_rows(
        [
            {
                "rel": "AlbumA/photo.jpg",
                "id": "asset-1",
                "dt": "2024-01-01",
                "metadata": {"camera": "cached"},
            }
        ]
    )
    pair_recorder = _PairRecorder()
    service = LibraryAssetLifecycleService(
        library_root,
        scan_service=pair_recorder,  # type: ignore[arg-type]
    )

    result = service.apply_move(
        moved=[(source, target)],
        source_root=album_a,
        destination_root=album_b,
    )

    rows = _rows(library_root)
    assert result.source_index_ok is True
    assert result.destination_index_ok is True
    assert "AlbumA/photo.jpg" not in rows
    assert rows["AlbumB/photo.jpg"]["id"] == "asset-1"
    assert rows["AlbumB/photo.jpg"]["parent_album_path"] == "AlbumB"
    assert pair_recorder.pair_roots == [library_root]


def test_delete_annotation_and_stale_trash_cleanup(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    album_root.mkdir(parents=True)
    trash_root.mkdir()
    (album_root / ".iPhoto").mkdir()
    (album_root / ".iphoto.album.json").write_text(
        json.dumps({"id": "album-a"}),
        encoding="utf-8",
    )
    source = album_root / "photo.jpg"
    target = trash_root / "photo.jpg"
    source.write_bytes(b"source")
    target.write_bytes(b"target")

    get_global_repository(library_root).write_rows(
        [
            {"rel": "AlbumA/photo.jpg", "id": "asset-1"},
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/missing.jpg",
                "id": "stale",
            },
        ]
    )
    service = LibraryAssetLifecycleService(
        library_root,
        scan_service=_PairRecorder(),  # type: ignore[arg-type]
    )

    result = service.apply_move(
        moved=[(source, target)],
        source_root=album_root,
        destination_root=trash_root,
        trash_root=trash_root,
    )

    rows = _rows(library_root)
    trash_rel = f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg"
    assert result.errors == []
    assert f"{RECENTLY_DELETED_DIR_NAME}/missing.jpg" not in rows
    assert rows[trash_rel]["original_rel_path"] == "AlbumA/photo.jpg"
    assert rows[trash_rel]["original_album_id"] == "album-a"
    assert rows[trash_rel]["original_album_subpath"] == "photo.jpg"


def test_delete_annotation_normalizes_legacy_album_manifest(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "LegacyAlbum"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    album_root.mkdir(parents=True)
    trash_root.mkdir()
    (album_root / ".iPhoto").mkdir()
    (album_root / ".iphoto.album").touch()
    source = album_root / "legacy.jpg"
    target = trash_root / "legacy.jpg"
    source.write_bytes(b"source")
    target.write_bytes(b"target")

    get_global_repository(library_root).write_rows(
        [{"rel": "LegacyAlbum/legacy.jpg", "id": "asset-legacy"}]
    )
    service = LibraryAssetLifecycleService(
        library_root,
        scan_service=_PairRecorder(),  # type: ignore[arg-type]
    )

    result = service.apply_move(
        moved=[(source, target)],
        source_root=album_root,
        destination_root=trash_root,
        trash_root=trash_root,
    )

    rows = _rows(library_root)
    manifest = json.loads((album_root / ".iphoto.album.json").read_text(encoding="utf-8"))
    trash_rel = f"{RECENTLY_DELETED_DIR_NAME}/legacy.jpg"
    assert result.errors == []
    assert isinstance(manifest["id"], str)
    assert manifest["id"]
    assert rows[trash_rel]["original_album_id"] == manifest["id"]
    assert rows[trash_rel]["original_album_subpath"] == "legacy.jpg"


def test_restore_clears_trash_metadata_from_destination_row(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    album_root.mkdir(parents=True)
    trash_root.mkdir()
    source = trash_root / "photo.jpg"
    target = album_root / "photo.jpg"
    source.write_bytes(b"source")
    target.write_bytes(b"target")

    get_global_repository(library_root).write_rows(
        [
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg",
                "id": "asset-1",
                "original_rel_path": "AlbumA/photo.jpg",
                "original_album_id": "album-a",
                "original_album_subpath": "photo.jpg",
            },
        ]
    )
    service = LibraryAssetLifecycleService(
        library_root,
        scan_service=_PairRecorder(),  # type: ignore[arg-type]
    )

    result = service.apply_move(
        moved=[(source, target)],
        source_root=trash_root,
        destination_root=album_root,
        trash_root=trash_root,
        is_restore=True,
    )

    rows = _rows(library_root)
    assert result.errors == []
    assert f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg" not in rows
    restored = rows["AlbumA/photo.jpg"]
    assert restored.get("original_rel_path") is None
    assert restored.get("original_album_id") is None
    assert restored.get("original_album_subpath") is None


def test_preserve_trash_metadata_merges_fields_into_fresh_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    trash_root.mkdir(parents=True)
    trash_rel = f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg"
    get_global_repository(library_root).write_rows(
        [
            {
                "rel": trash_rel,
                "id": "asset-1",
                "original_rel_path": "AlbumA/photo.jpg",
                "original_album_id": "album-a",
                "original_album_subpath": "photo.jpg",
            }
        ]
    )
    service = LibraryAssetLifecycleService(library_root)

    rows = service.preserve_trash_metadata(
        trash_root,
        [{"rel": trash_rel, "id": "fresh"}],
    )

    assert rows == [
        {
            "rel": trash_rel,
            "id": "fresh",
            "original_rel_path": "AlbumA/photo.jpg",
            "original_album_id": "album-a",
            "original_album_subpath": "photo.jpg",
        }
    ]


def test_cleanup_deleted_index_removes_missing_trash_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    trash_root.mkdir(parents=True)
    keep = trash_root / "keep.jpg"
    keep.write_bytes(b"data")

    present = f"{RECENTLY_DELETED_DIR_NAME}/keep.jpg"
    missing = f"{RECENTLY_DELETED_DIR_NAME}/missing.jpg"
    get_global_repository(library_root).write_rows(
        [
            {"rel": present, "id": "present"},
            {"rel": missing, "id": "missing"},
        ]
    )
    service = LibraryAssetLifecycleService(library_root)

    removed = service.cleanup_deleted_index(trash_root)

    assert removed == 1
    rows = _rows(library_root)
    assert list(rows) == [present]

    keep.unlink()
    removed_again = service.cleanup_deleted_index(trash_root)

    assert removed_again == 1
    assert _rows(library_root) == {}


def test_reconcile_missing_scan_rows_prunes_scope_after_scan_finalize(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    album_root.mkdir(parents=True)
    get_global_repository(library_root).write_rows(
        [
            {"rel": "AlbumA/keep.jpg", "id": "keep", "is_favorite": True},
            {"rel": "AlbumA/stale.jpg", "id": "stale"},
            {"rel": "AlbumB/other.jpg", "id": "other"},
        ]
    )
    service = LibraryAssetLifecycleService(library_root)

    removed = service.reconcile_missing_scan_rows(
        album_root,
        [{"rel": "AlbumA/keep.jpg", "id": "keep"}],
    )

    rows = _rows(library_root)
    assert removed == 1
    assert set(rows) == {"AlbumA/keep.jpg", "AlbumB/other.jpg"}
    assert bool(rows["AlbumA/keep.jpg"]["is_favorite"]) is True


def test_read_index_rows_by_rels_returns_library_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir(parents=True)
    get_global_repository(library_root).write_rows(
        [
            {"rel": "photo.jpg", "id": "photo"},
            {"rel": "motion.mov", "id": "motion"},
        ]
    )
    service = LibraryAssetLifecycleService(library_root)

    rows = service.read_index_rows_by_rels(["photo.jpg", "missing.jpg"])

    assert set(rows) == {"photo.jpg"}
    assert rows["photo.jpg"]["rel"] == "photo.jpg"
    assert rows["photo.jpg"]["id"] == "photo"
