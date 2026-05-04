from __future__ import annotations

from pathlib import Path

from iPhoto.bootstrap.library_asset_operation_service import (
    LibraryAssetOperationService,
)
from iPhoto.config import RECENTLY_DELETED_DIR_NAME


class _FakeLifecycleService:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.rows_by_rel: dict[str, dict] = {}
        self.read_roots: list[Path] = []
        self.read_rels: list[list[str]] = []

    def read_restore_index_rows(self, trash_root: Path) -> list[dict]:
        self.read_roots.append(Path(trash_root))
        return [dict(row) for row in self.rows]

    def read_index_rows_by_rels(self, rels) -> dict[str, dict]:
        materialized = list(rels)
        self.read_rels.append(materialized)
        return {
            rel: dict(self.rows_by_rel[rel])
            for rel in materialized
            if rel in self.rows_by_rel
        }


def test_move_plan_dedupes_sources_and_rejects_same_destination(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    destination_root = library_root / "AlbumB"
    album_root.mkdir(parents=True)
    destination_root.mkdir()
    asset = album_root / "photo.jpg"
    asset.write_bytes(b"data")
    lifecycle = _FakeLifecycleService()
    service = LibraryAssetOperationService(
        library_root,
        lifecycle_service=lifecycle,  # type: ignore[arg-type]
    )

    plan = service.plan_move_request(
        [asset, asset],
        destination_root,
        current_album_root=album_root,
    )

    assert plan.accepted is True
    assert plan.sources == [asset.resolve()]
    assert plan.source_root == album_root.resolve()
    assert plan.destination_root == destination_root.resolve()
    assert plan.asset_lifecycle_service is lifecycle

    rejected = service.plan_move_request(
        [asset],
        album_root,
        current_album_root=album_root,
    )

    assert rejected.accepted is False
    assert rejected.finished_message == "Files are already located in this album."


def test_delete_plan_skips_missing_sources_and_adds_live_companion(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    album_root.mkdir(parents=True)
    trash_root.mkdir()
    still = album_root / "IMG_0001.HEIC"
    motion = album_root / "IMG_0001.MOV"
    missing = album_root / "missing.jpg"
    still.write_bytes(b"still")
    motion.write_bytes(b"motion")
    service = LibraryAssetOperationService(
        library_root,
        lifecycle_service=_FakeLifecycleService(),  # type: ignore[arg-type]
    )

    plan = service.plan_delete_request(
        [still, missing],
        trash_root=trash_root,
        metadata_lookup=lambda path: {
            "is_live": True,
            "live_motion_abs": str(motion),
        }
        if path == still.resolve()
        else None,
    )

    assert plan.accepted is True
    assert plan.operation == "delete"
    assert plan.source_root == library_root.resolve()
    assert plan.destination_root == trash_root.resolve()
    assert plan.trash_root == trash_root.resolve()
    assert plan.sources == [still.resolve(), motion.resolve()]
    assert plan.errors == []

    empty = service.plan_delete_request([missing], trash_root=trash_root)

    assert empty.accepted is False
    assert empty.finished_message == "No items were deleted."
    assert empty.errors == []


def test_restore_plan_uses_original_rel_destination(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    album_root.mkdir(parents=True)
    trash_root.mkdir()
    trashed = trash_root / "photo.jpg"
    trashed.write_bytes(b"data")
    lifecycle = _FakeLifecycleService(
        [
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg",
                "original_rel_path": "AlbumA/photo.jpg",
            }
        ]
    )
    service = LibraryAssetOperationService(
        library_root,
        lifecycle_service=lifecycle,  # type: ignore[arg-type]
    )

    plan = service.plan_restore_request([trashed], trash_root=trash_root)

    assert plan.errors == []
    assert len(plan.batches) == 1
    batch = plan.batches[0]
    assert batch.accepted is True
    assert batch.operation == "restore"
    assert batch.source_root == trash_root.resolve()
    assert batch.destination_root == album_root.resolve()
    assert batch.sources == [trashed.resolve()]


def test_restore_plan_recovers_stale_rows_and_same_stem_motion(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    trash_root.mkdir(parents=True)
    still = trash_root / "IMG_3686.HEIC"
    motion = trash_root / "IMG_3686.MOV"
    still.write_bytes(b"still")
    motion.write_bytes(b"motion")
    lifecycle = _FakeLifecycleService()
    lifecycle.rows_by_rel = {
        "IMG_3686.HEIC": {"rel": "IMG_3686.HEIC"},
        "IMG_3686.MOV": {"rel": "IMG_3686.MOV"},
    }
    service = LibraryAssetOperationService(
        library_root,
        lifecycle_service=lifecycle,  # type: ignore[arg-type]
    )

    plan = service.plan_restore_request([still], trash_root=trash_root)

    assert plan.errors == []
    assert lifecycle.read_rels == [["IMG_3686.HEIC", "IMG_3686.MOV"]]
    assert len(plan.batches) == 1
    assert plan.batches[0].destination_root == library_root.resolve()
    assert plan.batches[0].sources == [still.resolve(), motion.resolve()]


def test_restore_plan_uses_album_manifest_id_and_prompt_fallback(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    missing_album_root = library_root / "Missing"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    album_root.mkdir(parents=True)
    trash_root.mkdir()
    (album_root / ".iphoto.album.json").write_text(
        '{"id": "album-a"}',
        encoding="utf-8",
    )
    trashed = trash_root / "photo.jpg"
    orphaned = trash_root / "orphan.jpg"
    trashed.write_bytes(b"data")
    orphaned.write_bytes(b"data")
    lifecycle = _FakeLifecycleService(
        [
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg",
                "original_album_id": "album-a",
                "original_album_subpath": "photo.jpg",
            },
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/orphan.jpg",
                "original_rel_path": "Missing/orphan.jpg",
            },
        ]
    )
    service = LibraryAssetOperationService(
        library_root,
        lifecycle_service=lifecycle,  # type: ignore[arg-type]
    )

    plan = service.plan_restore_request(
        [trashed, orphaned],
        trash_root=trash_root,
        restore_to_root_prompt=lambda filename: filename == "orphan.jpg",
    )

    assert plan.errors == []
    destinations = {batch.destination_root for batch in plan.batches}
    assert destinations == {album_root.resolve(), library_root.resolve()}
    assert not missing_album_root.exists()


def test_restore_plan_revalidates_cached_album_root_after_rename(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "AlbumA"
    renamed_root = library_root / "AlbumB"
    trash_root = library_root / RECENTLY_DELETED_DIR_NAME
    album_root.mkdir(parents=True)
    trash_root.mkdir()
    (album_root / ".iphoto.album.json").write_text(
        '{"id": "album-a"}',
        encoding="utf-8",
    )
    first = trash_root / "first.jpg"
    second = trash_root / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    lifecycle = _FakeLifecycleService(
        [
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/first.jpg",
                "original_album_id": "album-a",
                "original_album_subpath": "first.jpg",
            },
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/second.jpg",
                "original_album_id": "album-a",
                "original_album_subpath": "second.jpg",
            },
        ]
    )
    service = LibraryAssetOperationService(
        library_root,
        lifecycle_service=lifecycle,  # type: ignore[arg-type]
    )

    first_plan = service.plan_restore_request([first], trash_root=trash_root)

    assert first_plan.errors == []
    assert first_plan.batches[0].destination_root == album_root.resolve()

    album_root.rename(renamed_root)

    second_plan = service.plan_restore_request([second], trash_root=trash_root)

    assert second_plan.errors == []
    assert second_plan.batches[0].destination_root == renamed_root.resolve()
    assert not album_root.exists()
