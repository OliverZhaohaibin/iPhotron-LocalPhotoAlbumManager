from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.index_sync_service import ensure_links, prune_index_scope


@pytest.fixture(autouse=True)
def _reset_global_repo() -> None:
    reset_global_repository()
    yield
    reset_global_repository()


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
    )

    assert removed == 2
    remaining = {row["rel"] for row in store.read_all(filter_hidden=False)}
    assert remaining == {"album-a/keep.jpg", "album-b/other.jpg"}


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

    ensure_links(album_root, rows)

    data = {row["rel"]: row for row in store.read_all(filter_hidden=False)}
    assert data["photo.heic"]["live_partner_rel"] == "motion.mov"
    assert data["motion.mov"]["live_partner_rel"] == "photo.heic"
    assert data["motion.mov"]["live_role"] == 1
    assert data["other.jpg"]["live_partner_rel"] is None
