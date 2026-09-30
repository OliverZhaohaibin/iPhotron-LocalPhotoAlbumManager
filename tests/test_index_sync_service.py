from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.domain.models.core import LiveGroup
from iPhoto.index_sync_service import ensure_links, prune_index_scope, sync_live_roles_to_db


@pytest.fixture(autouse=True)
def _reset_global_repo() -> None:
    reset_global_repository()
    yield
    reset_global_repository()


def test_sync_live_roles_updates_still_and_motion_rows(tmp_path: Path) -> None:
    album_root = tmp_path / "album"
    album_root.mkdir()
    store = get_global_repository(album_root)
    store.write_rows(
        [
            {"rel": "photo.jpg", "id": "photo"},
            {"rel": "video.mov", "id": "video"},
            {"rel": "other.png", "id": "other"},
        ]
    )
    group = LiveGroup(
        id="group-1",
        still="photo.jpg",
        motion="video.mov",
        confidence=1.0,
        content_id="content-1",
        still_image_time=0.0,
    )

    sync_live_roles_to_db(album_root, [group], repository=store)

    rows = {row["rel"]: row for row in store.read_all(filter_hidden=False)}
    assert rows["photo.jpg"]["live_role"] == 0
    assert rows["photo.jpg"]["live_partner_rel"] == "video.mov"
    assert rows["video.mov"]["live_role"] == 1
    assert rows["video.mov"]["live_partner_rel"] == "photo.jpg"
    assert rows["other.png"]["live_partner_rel"] is None


def test_sync_live_roles_empty_result_clears_existing_roles(tmp_path: Path) -> None:
    album_root = tmp_path / "album"
    album_root.mkdir()
    store = get_global_repository(album_root)
    store.write_rows([{"rel": "photo.jpg"}, {"rel": "video.mov"}])
    store.apply_live_role_updates([("video.mov", 1, "photo.jpg")])

    sync_live_roles_to_db(album_root, [], repository=store)

    rows = {row["rel"]: row for row in store.read_all(filter_hidden=False)}
    assert rows["video.mov"]["live_role"] == 0
    assert rows["video.mov"]["live_partner_rel"] is None


def test_sync_live_roles_skips_incomplete_groups(tmp_path: Path) -> None:
    album_root = tmp_path / "album"
    album_root.mkdir()
    store = get_global_repository(album_root)
    store.write_rows([{"rel": "only.jpg"}, {"rel": "missing.mov"}])
    incomplete_group = LiveGroup(
        id="group-1",
        still="only.jpg",
        motion="",
        confidence=0.5,
        content_id=None,
        still_image_time=None,
    )

    sync_live_roles_to_db(album_root, [incomplete_group], repository=store)

    rows = {row["rel"]: row for row in store.read_all(filter_hidden=False)}
    assert rows["only.jpg"]["live_partner_rel"] is None
    assert rows["missing.mov"]["live_partner_rel"] is None


def test_sync_live_roles_only_clears_target_album_prefix(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)
    store.write_rows(
        [
            {"rel": "album/photo.jpg"},
            {"rel": "album/video.mov"},
            {"rel": "other/keep.jpg"},
        ]
    )
    store.apply_live_role_updates([("other/keep.jpg", 1, "other/partner.mov")])
    group = LiveGroup(
        id="group-1",
        still="photo.jpg",
        motion="video.mov",
        confidence=1.0,
        content_id=None,
        still_image_time=None,
    )

    sync_live_roles_to_db(
        album_root,
        [group],
        library_root=library_root,
        repository=store,
    )

    rows = {row["rel"]: row for row in store.read_all(filter_hidden=False)}
    assert rows["album/photo.jpg"]["live_partner_rel"] == "album/video.mov"
    assert rows["album/video.mov"]["live_partner_rel"] == "album/photo.jpg"
    assert rows["other/keep.jpg"]["live_role"] == 1


def test_prune_index_scope_removes_only_rows_within_scan_prefix(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    album_a = library_root / "album-a"
    album_b = library_root / "album-b"
    album_a.mkdir(parents=True)
    album_b.mkdir(parents=True)

    store = get_global_repository(library_root)
    store.write_rows(
        [
            {"rel": "album-a/keep.jpg", "id": "keep"},
            {"rel": "album-a/stale.jpg", "id": "stale"},
            {"rel": "album-a/motion.mov", "id": "motion", "live_role": 1},
            {"rel": "album-b/other.jpg", "id": "other"},
        ]
    )

    removed = prune_index_scope(
        album_a,
        [{"rel": "album-a/keep.jpg", "id": "keep"}],
        library_root=library_root,
        repository=store,
    )

    assert removed == 2
    remaining = {row["rel"] for row in store.read_all(filter_hidden=False)}
    assert remaining == {"album-a/keep.jpg", "album-b/other.jpg"}


def test_prune_index_scope_preserves_concurrent_non_scan_writes(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir(parents=True)
    store = get_global_repository(library_root)
    store.write_rows(
        [
            {"rel": "AlbumA/stale-from-scan.jpg", "id": "stale", "scan_job_id": "scan_1"},
            {"rel": "AlbumB/moved-during-scan.jpg", "id": "moved", "scan_job_id": None},
        ]
    )

    removed = prune_index_scope(
        library_root,
        [],
        library_root=library_root,
        repository=store,
        preserve_modified_after_ms=1,
        current_scan_job_id="scan_1",
    )

    remaining = {row["rel"] for row in store.read_all(filter_hidden=False)}
    assert removed == 1
    assert remaining == {"AlbumB/moved-during-scan.jpg"}


def test_prune_index_scope_keeps_excluded_trash_rows_during_library_rescan(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir(parents=True)
    trash_asset = library_root / RECENTLY_DELETED_DIR_NAME / "photo.jpg"
    trash_asset.parent.mkdir(parents=True)
    trash_asset.write_bytes(b"trash")

    store = get_global_repository(library_root)
    store.write_rows(
        [
            {"rel": "AlbumA/keep.jpg", "id": "keep"},
            {"rel": "AlbumA/stale.jpg", "id": "stale"},
            {"rel": f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg", "id": "trash"},
        ]
    )

    removed = prune_index_scope(
        library_root,
        [{"rel": "AlbumA/keep.jpg", "id": "keep"}],
        library_root=library_root,
        repository=store,
        exclude_globs=[f"**/{RECENTLY_DELETED_DIR_NAME}/**"],
    )

    assert removed == 1
    remaining = {row["rel"] for row in store.read_all(filter_hidden=False)}
    assert remaining == {"AlbumA/keep.jpg", f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg"}


def test_prune_index_scope_keeps_missing_trash_rows_during_library_rescan(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir(parents=True)

    store = get_global_repository(library_root)
    store.write_rows(
        [
            {"rel": "AlbumA/keep.jpg", "id": "keep"},
            {"rel": "AlbumA/stale.jpg", "id": "stale"},
            {"rel": f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg", "id": "trash"},
        ]
    )

    removed = prune_index_scope(
        library_root,
        [{"rel": "AlbumA/keep.jpg", "id": "keep"}],
        library_root=library_root,
        repository=store,
        exclude_globs=[f"**/{RECENTLY_DELETED_DIR_NAME}/**"],
    )

    assert removed == 1
    remaining = {row["rel"] for row in store.read_all(filter_hidden=False)}
    assert remaining == {"AlbumA/keep.jpg", f"{RECENTLY_DELETED_DIR_NAME}/photo.jpg"}


def test_prune_index_scope_removes_rows_under_non_trash_excludes_during_library_rescan(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir(parents=True)
    secret_asset = library_root / "secret" / "photo.jpg"
    secret_asset.parent.mkdir(parents=True)
    secret_asset.write_bytes(b"secret")

    store = get_global_repository(library_root)
    store.write_rows(
        [
            {"rel": "AlbumA/keep.jpg", "id": "keep"},
            {"rel": "secret/photo.jpg", "id": "secret"},
        ]
    )

    removed = prune_index_scope(
        library_root,
        [{"rel": "AlbumA/keep.jpg", "id": "keep"}],
        library_root=library_root,
        repository=store,
        exclude_globs=["secret/**"],
    )

    assert removed == 1
    remaining = {row["rel"] for row in store.read_all(filter_hidden=False)}
    assert remaining == {"AlbumA/keep.jpg"}


def test_ensure_links_keeps_db_live_roles_when_derived_snapshot_write_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    album_root = tmp_path / "album"
    album_root.mkdir()

    store = get_global_repository(album_root)
    store.write_rows(
        [
            {"rel": "photo.heic", "id": "photo"},
            {"rel": "motion.mov", "id": "motion"},
            {"rel": "other.jpg", "id": "other"},
        ]
    )

    rows = [
        {
            "rel": "photo.heic",
            "mime": "image/heic",
            "content_id": "CID-1",
            "dt": "2024-01-01T00:00:00Z",
        },
        {
            "rel": "motion.mov",
            "mime": "video/quicktime",
            "content_id": "CID-1",
            "dt": "2024-01-01T00:00:00Z",
            "dur": 1.5,
        },
    ]

    monkeypatch.setattr(
        "iPhoto.index_sync_service.write_links",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
    )

    ensure_links(album_root, rows, repository=store)

    data = {row["rel"]: row for row in store.read_all(filter_hidden=False)}
    assert data["photo.heic"]["live_partner_rel"] == "motion.mov"
    assert data["motion.mov"]["live_partner_rel"] == "photo.heic"
    assert data["motion.mov"]["live_role"] == 1
    assert data["other.jpg"]["live_partner_rel"] is None
