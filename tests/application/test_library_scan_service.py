from __future__ import annotations

import sqlite3
import json
import time
from unittest.mock import patch
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

import iPhoto.bootstrap.library_scan_service as scan_service_module
from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.utils.hashutils import compute_file_id


class _Scanner:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        fail_after_rows: bool = False,
    ) -> None:
        self._rows = rows
        self._fail_after_rows = fail_after_rows

    def scan(
        self,
        _root: Path,
        _include: Iterable[str],
        _exclude: Iterable[str],
        **_kwargs: object,
    ):
        yield from self._rows
        if self._fail_after_rows:
            raise RuntimeError("scan failed")


class _CountingRepository:
    library_root: Path
    path: Path

    def __init__(self, count: int) -> None:
        self.library_root = Path("/tmp/library")
        self.path = self.library_root / ".iPhoto" / "global_index.db"
        self.count_value = count
        self.count_calls: list[dict[str, Any]] = []
        self.read_all_called = False
        self.read_album_assets_called = False

    def count(self, **kwargs: Any) -> int:
        self.count_calls.append(kwargs)
        return self.count_value

    def read_all(self, *_args: Any, **_kwargs: Any):
        self.read_all_called = True
        raise AssertionError("lazy open must not hydrate read_all")

    def read_album_assets(self, *_args: Any, **_kwargs: Any):
        self.read_album_assets_called = True
        raise AssertionError("lazy open must not hydrate read_album_assets")

    def merge_scan_rows(self, rows):
        return list(rows)


class _FavoriteFailingRepository:
    library_root: Path
    path: Path

    def __init__(self) -> None:
        self.library_root = Path("/tmp/library")
        self.path = self.library_root / ".iPhoto" / "global_index.db"
        self.calls = 0

    def sync_favorites(self, _featured) -> None:
        self.calls += 1
        raise sqlite3.Error("db locked")


@pytest.fixture(autouse=True)
def clean_global_repository():
    reset_global_repository()
    yield
    reset_global_repository()


@pytest.mark.parametrize("replacement", [False, True], ids=["missing", "replaced-by-file"])
def test_missing_album_reconciliation_clears_peers_and_preserves_other_state(
    tmp_path: Path, replacement: bool
) -> None:
    if replacement:
        (tmp_path / "Gone").write_bytes(b"an album is now a file")
    trash_rel = f"{RECENTLY_DELETED_DIR_NAME}/kept.jpg"
    store = get_global_repository(tmp_path)
    store.write_rows([
        {"rel": "Gone/motion.mov", "id": "motion", "live_role": 1,
         "live_partner_rel": "photo.jpg"},
        {"rel": "photo.jpg", "id": "still", "live_partner_rel": "Gone/motion.mov",
         "is_favorite": True},
        {"rel": trash_rel, "id": "trashed", "is_deleted": True,
         "original_rel_path": "Original/kept.jpg", "is_favorite": True},
    ])
    trashed = store.get_rows_by_rels([trash_rel])

    assert LibraryScanService(tmp_path).reconcile_missing_album_scope(tmp_path / "Gone")

    rows = store.get_rows_by_rels(["Gone/motion.mov", "photo.jpg"])
    assert set(rows) == {"photo.jpg"}
    assert rows["photo.jpg"]["live_partner_rel"] is None
    assert rows["photo.jpg"]["is_favorite"]
    assert store.get_rows_by_rels([trash_rel]) == trashed


@pytest.mark.parametrize("change", ["recreated", "inaccessible", "cancelled", "disconnected"])
def test_missing_album_reconciliation_rechecks_before_removing_rows(
    tmp_path: Path, change: str
) -> None:
    album = tmp_path / "Gone"
    store = get_global_repository(tmp_path)
    store.write_rows([{"rel": "Gone/photo.jpg", "id": "kept", "is_favorite": True}])
    before = store.get_rows_by_rels(["Gone/photo.jpg"])
    service = LibraryScanService(tmp_path)
    real_stat = Path.stat
    stat_calls = 0

    def changed_stat(path: Path, *args, **kwargs):
        nonlocal stat_calls
        if change == "disconnected" and path == tmp_path and stat_calls > 1:
            raise FileNotFoundError(2, "library disconnected during reconciliation", str(path))
        if path == album:
            stat_calls += 1
            if stat_calls > 1:
                if change == "inaccessible":
                    raise PermissionError("temporarily inaccessible")
                if change == "recreated":
                    album.mkdir()
        return real_stat(path, *args, **kwargs)

    with patch.object(Path, "stat", changed_stat):
        if change in {"inaccessible", "disconnected"}:
            error = PermissionError if change == "inaccessible" else scan_service_module.AlbumNotFoundError
            with pytest.raises(error):
                service.reconcile_missing_album_scope(album)
        else:
            assert service.reconcile_missing_album_scope(
                album, is_cancelled=lambda: change == "cancelled"
            ) is (change == "cancelled")

    assert store.get_rows_by_rels(["Gone/photo.jpg"]) == before


@pytest.mark.parametrize("matching_content", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_missing_album_reconciliation_only_relocates_matching_assets(
    tmp_path: Path, matching_content: bool, nested: bool
) -> None:
    parent = tmp_path / "Parent" if nested else tmp_path
    old = parent / "Old"
    old.mkdir(parents=True)
    source = old / "photo.jpg"
    source.write_bytes(b"original media")
    source_rel = source.relative_to(tmp_path).as_posix()
    destination_rel = (parent / "New" / "photo.jpg").relative_to(tmp_path).as_posix()
    asset_id = f"as_{compute_file_id(source)}"
    store = get_global_repository(tmp_path)
    gps = {"lat": 52.5, "lon": 13.4}
    store.write_rows([{
        "rel": source_rel, "id": asset_id, "is_favorite": True,
        "gps": gps, "location": "Local place", "thumbnail_state": "ready",
        "thumb_cache_key": "old-path", "face_status": "done", "pet_status": "done",
    }])
    source.unlink()
    old.rmdir()
    new = parent / "New"
    new.mkdir()
    (new / "photo.jpg").write_bytes(b"original media" if matching_content else b"different media")

    assert LibraryScanService(tmp_path).reconcile_missing_album_scope(
        old, relocation_roots=(new,)
    )

    assert not store.get_rows_by_rels([source_rel])
    relocated = store.get_rows_by_rels([destination_rel])
    if matching_content:
        row = relocated[destination_rel]
        assert row["is_favorite"]
        assert row["gps"] == gps
        assert row["location"] == "Local place"
        assert row["thumbnail_state"] == "stale"
        assert row["thumb_cache_key"] is None
        assert row["face_status"] is None
        assert row["pet_status"] is None
        # The state has already been committed even if destination scanning is
        # interrupted or the process closes before it starts.
        reset_global_repository()
        assert get_global_repository(tmp_path).get_rows_by_rels([destination_rel]) == relocated
    else:
        assert not relocated


def test_ambiguous_album_relocation_does_not_discard_user_state(tmp_path: Path) -> None:
    candidates = [tmp_path / name for name in ("New", "Copy")]
    for candidate in candidates:
        candidate.mkdir()
        (candidate / "photo.jpg").write_bytes(b"same media")
    store = get_global_repository(tmp_path)
    store.write_rows([{
        "rel": "Old/photo.jpg", "id": f"as_{compute_file_id(candidates[0] / 'photo.jpg')}",
        "is_favorite": True, "location": "Local place",
    }])
    before = store.get_rows_by_rels(["Old/photo.jpg"])

    with pytest.raises(ValueError, match="Ambiguous relocated asset"):
        LibraryScanService(tmp_path).reconcile_missing_album_scope(
            tmp_path / "Old", relocation_roots=candidates
        )

    assert store.get_rows_by_rels(["Old/photo.jpg"]) == before


def test_scan_album_is_atomic_until_finalize(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    store = get_global_repository(library_root)
    store.write_rows([{"rel": "existing.jpg", "id": "existing"}])
    service = LibraryScanService(
        library_root,
        scanner=_Scanner([{"rel": "new.jpg", "id": "new"}], fail_after_rows=True),
    )

    with pytest.raises(RuntimeError, match="scan failed"):
        service.scan_album(library_root, persist_chunks=False)

    assert {row["rel"] for row in store.read_all(filter_hidden=False)} == {
        "existing.jpg"
    }


def test_root_media_pairing_updates_only_affected_roles_and_links(tmp_path: Path) -> None:
    root = tmp_path
    service = LibraryScanService(root)
    store = get_global_repository(root)
    nested = ("Album/image.jpg", "Album/motion.mov")
    cross = ("cross.jpg", "Album/cross.mov")
    direct = ("new.jpg", "new.mov")

    def pair_rows(paths: tuple[str, str], content_id: str) -> list[dict[str, Any]]:
        return [
            {"rel": paths[0], "mime": "image/jpeg", "content_id": content_id},
            {"rel": paths[1], "mime": "video/quicktime", "content_id": content_id},
        ]

    (root / "Album").mkdir()
    for rel in (*nested, *cross, *direct):
        (root / rel).touch()
    store.write_rows([*pair_rows(nested, "nested"), *pair_rows(cross, "cross")])
    service.pair_album(root)
    old_roles = store.get_rows_by_rels(nested)

    service.finalize_scan_result(root, pair_rows(direct, "direct"), root_only=True)
    roles = store.get_rows_by_rels((*nested, *cross, *direct))
    assert all(roles[rel] == old_roles[rel] for rel in nested)
    assert roles[direct[1]]["live_partner_rel"] == direct[0]
    assert roles[cross[1]]["live_partner_rel"] == cross[0]

    (root / cross[0]).unlink()
    service.finalize_scan_result(root, pair_rows(direct, "direct"), root_only=True)
    [former_motion] = store.get_rows_by_rels([cross[1]]).values()
    assert former_motion["live_role"] == 0
    assert former_motion["live_partner_rel"] is None
    links = json.loads((root / ".iPhoto" / "links.json").read_text(encoding="utf-8"))
    assert {(group["still"], group["motion"]) for group in links["live_groups"]} == {
        nested, direct,
    }


@pytest.mark.parametrize("root_is_still", [False, True])
def test_root_scan_discovers_unpaired_cross_album_content_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_is_still: bool
) -> None:
    service = LibraryScanService(tmp_path)
    store = get_global_repository(tmp_path)
    (tmp_path / "Album").mkdir()
    root_rel = "new.jpg" if root_is_still else "new.mov"
    peer_rel = "Album/motion.mov" if root_is_still else "Album/still.jpg"
    root_mime = "image/jpeg" if root_is_still else "video/quicktime"
    peer_mime = "video/quicktime" if root_is_still else "image/jpeg"
    for rel in (root_rel, peer_rel, "Album/saved.jpg", "Album/saved.mov"):
        (tmp_path / rel).touch()
    store.write_rows([
        {"rel": peer_rel, "mime": peer_mime, "content_id": "  ABc-ß\t"},
        {"rel": "Album/saved.jpg", "mime": "image/jpeg", "content_id": "abc-ss",
         "live_partner_rel": "Album/saved.mov"},
        {"rel": "Album/saved.mov", "mime": "video/quicktime", "content_id": "abc-ss",
         "live_role": 1, "live_partner_rel": "Album/saved.jpg"},
    ])
    saved = store.get_rows_by_rels(["Album/saved.jpg", "Album/saved.mov"])
    # Start with a complete snapshot so its root-only refresh is also exercised.
    links = tmp_path / ".iPhoto" / "links.json"
    links.write_text(json.dumps({"schema": "iPhoto/links@1", "live_groups": []}))
    monkeypatch.setattr(store, "read_all", lambda **_kw: pytest.fail("full library read"))

    service.finalize_scan_result(
        tmp_path,
        [{"rel": root_rel, "mime": root_mime, "content_id": "abc-ss"}],
        root_only=True,
    )

    rows = store.get_rows_by_rels([root_rel, peer_rel])
    assert rows[root_rel]["live_partner_rel"] == peer_rel
    assert rows[peer_rel]["live_partner_rel"] == root_rel
    assert rows[root_rel]["live_role"] == int(not root_is_still)
    assert rows[peer_rel]["live_role"] == int(root_is_still)
    assert store.get_rows_by_rels(saved) == saved
    groups = json.loads(links.read_text())["live_groups"]
    assert len(groups) == 1
    assert {groups[0]["still"], groups[0]["motion"]} == {root_rel, peer_rel}


def test_subalbum_scan_prefixes_library_relative_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    service = LibraryScanService(
        library_root,
        scanner=_Scanner([{"rel": "a.jpg", "id": "asset-a"}]),
    )

    result = service.scan_album(album_root, persist_chunks=False)
    service.finalize_scan(album_root, result.rows)

    store = get_global_repository(library_root)
    assert result.rows == [{"rel": "album/a.jpg", "id": "asset-a"}]
    assert [row["rel"] for row in store.read_album_assets("album")] == [
        "album/a.jpg"
    ]


def test_scan_album_visible_publish_batches_are_small_enough(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    rows = [
        {
                "rel": f"{index}.jpg",
                "id": f"asset-{index}",
                "thumbnail_state": "ready",
                "micro_thumbnail": b"thumb",
                "thumb_cache_key": f"thumb-{index}",
            }
            for index in range(501)
        ]
    emitted_sizes: list[int] = []
    service = LibraryScanService(library_root, scanner=_Scanner(rows))

    result = service.scan_album(
        library_root,
        persist_chunks=True,
        scan_batch_callback=lambda batch: emitted_sizes.append(len(batch.rows)),
    )

    assert len(result.rows) == 501
    assert emitted_sizes == [100, 100, 100, 100, 100, 1]


def test_scan_scope_complete_uses_latest_scan_job_status(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)
    service = LibraryScanService(library_root)

    store.create_scan_job(
        job_id="scan_root_complete",
        root=library_root.as_posix(),
        scope="library",
    )
    store.update_scan_job_stage("scan_root_complete", status="completed", finished=True)

    assert service.is_scan_scope_complete(library_root) is True
    assert service.is_scan_scope_complete(album_root) is True

    time.sleep(0.002)
    store.create_scan_job(
        job_id="scan_album_cancelled",
        root=album_root.as_posix(),
        scope="album",
        status="running",
    )
    store.update_scan_job_stage("scan_album_cancelled", status="cancelled", finished=True)

    assert service.is_scan_scope_complete(album_root) is False


def test_scan_scope_complete_waits_for_finalize_success(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "a.jpg").write_bytes(b"image")
    store = get_global_repository(library_root)
    service = LibraryScanService(
        library_root,
        scanner=_Scanner([{"rel": "a.jpg", "id": "asset-a"}]),
    )

    result = service.scan_album(library_root, persist_chunks=False)

    assert result.scan_job_id
    assert service.is_scan_scope_complete(library_root) is False

    service.finalize_scan_result(
        library_root,
        result.rows,
        pair_live=False,
        current_scan_job_id=result.scan_job_id,
    )

    job = store.latest_scan_job(root=library_root.as_posix(), scope="library")
    assert job is not None
    assert job["status"] == "completed"
    assert job["stage"] == "db_commit"
    assert service.is_scan_scope_complete(library_root) is True


def test_finalize_scan_result_failure_keeps_scope_incomplete(
    monkeypatch,
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "a.jpg").write_bytes(b"image")
    store = get_global_repository(library_root)
    service = LibraryScanService(
        library_root,
        scanner=_Scanner([{"rel": "a.jpg", "id": "asset-a"}]),
    )
    result = service.scan_album(library_root, persist_chunks=False)

    def fail_finalize(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("finalize failed")

    monkeypatch.setattr(service, "finalize_scan", fail_finalize)

    with pytest.raises(RuntimeError, match="finalize failed"):
        service.finalize_scan_result(
            library_root,
            result.rows,
            pair_live=False,
            current_scan_job_id=result.scan_job_id,
        )

    job = store.latest_scan_job(root=library_root.as_posix(), scope="library")
    assert job is not None
    assert job["status"] == "failed"
    assert service.is_scan_scope_complete(library_root) is False


def test_finalize_scan_result_preserves_move_written_rows_during_scan(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_a = library_root / "AlbumA"
    album_b = library_root / "AlbumB"
    album_a.mkdir(parents=True)
    album_b.mkdir()
    moved_target = album_b / "a.jpg"
    moved_target.write_bytes(b"moved")
    store = get_global_repository(library_root)
    store.write_rows(
        [
            {"rel": "AlbumA/a.jpg", "id": "old", "scan_job_id": "scan_1"},
            {"rel": "AlbumB/a.jpg", "id": "moved", "scan_job_id": None},
        ]
    )
    service = LibraryScanService(library_root)

    materialized = service.finalize_scan_result(
        library_root,
        [{"rel": "AlbumA/a.jpg", "id": "old", "scan_job_id": "scan_1"}],
        pair_live=False,
        preserve_modified_after_ms=1,
        current_scan_job_id="scan_1",
    )

    assert materialized == []
    remaining = {row["rel"] for row in store.read_all(filter_hidden=False)}
    assert remaining == {"AlbumB/a.jpg"}


def test_finalize_scan_does_not_prune_stale_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)
    store.write_rows(
        [
            {"rel": "album/keep.jpg", "id": "keep", "is_favorite": True},
            {"rel": "album/stale.jpg", "id": "stale"},
        ]
    )
    service = LibraryScanService(library_root)

    service.finalize_scan(album_root, [{"rel": "album/keep.jpg", "id": "keep"}])

    rows = {
        row["rel"]: row
        for row in store.read_album_assets(
            "album",
            include_subalbums=True,
            filter_hidden=False,
        )
    }
    assert set(rows) == {"album/keep.jpg", "album/stale.jpg"}
    assert bool(rows["album/keep.jpg"]["is_favorite"]) is True


def test_finalize_scan_preserves_subalbum_live_pairs_across_repeated_rescans(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    service = LibraryScanService(library_root)
    rows = [
        {
            "rel": "album/IMG_0001.HEIC",
            "id": "still",
            "mime": "image/heic",
            "dt": "2024-01-01T00:00:00Z",
            "ts": 1,
            "bytes": 1,
            "content_id": "live-content",
        },
        {
            "rel": "album/IMG_0001.MOV",
            "id": "motion",
            "mime": "video/quicktime",
            "dt": "2024-01-01T00:00:00Z",
            "ts": 1,
            "bytes": 1,
            "content_id": "live-content",
        },
    ]

    service.finalize_scan(album_root, rows)
    service.finalize_scan(album_root, rows)
    groups = service.pair_album(album_root)

    store = get_global_repository(library_root)
    indexed = {
        row["rel"]: row
        for row in store.read_all(filter_hidden=False)
    }
    assert [(group.still, group.motion) for group in groups] == [
        ("IMG_0001.HEIC", "IMG_0001.MOV")
    ]
    assert indexed["album/IMG_0001.HEIC"]["live_role"] == 0
    assert indexed["album/IMG_0001.HEIC"]["live_partner_rel"] == "album/IMG_0001.MOV"
    assert indexed["album/IMG_0001.MOV"]["live_role"] == 1
    assert indexed["album/IMG_0001.MOV"]["live_partner_rel"] == "album/IMG_0001.HEIC"


def test_report_album_uses_session_repository_and_links(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    store = get_global_repository(library_root)
    store.write_rows([{"rel": "a.jpg", "id": "asset-a"}])
    service = LibraryScanService(library_root, scanner=_Scanner([]))

    report = service.report_album(library_root)

    assert report.title == "library"
    assert report.asset_count == 1
    assert report.live_pair_count == 0


def test_prepare_album_open_lazy_uses_scoped_count_without_hydration(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    repository = _CountingRepository(count=5)
    service = LibraryScanService(
        library_root,
        repository_factory=lambda _root: repository,
    )

    result = service.prepare_album_open(
        library_root,
        autoscan=False,
        hydrate_index=False,
    )

    assert result.asset_count == 5
    assert result.rows is None
    assert result.scanned is False
    assert repository.count_calls == [
        {"filter_hidden": True},
    ]
    assert repository.read_all_called is False
    assert repository.read_album_assets_called is False


def test_prepare_album_open_autoscan_uses_shared_scan_and_finalize(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "a.jpg").write_bytes(b"image")
    service = LibraryScanService(
        library_root,
        scanner=_Scanner([{"rel": "a.jpg", "id": "asset-a"}]),
    )

    result = service.prepare_album_open(
        library_root,
        autoscan=True,
        hydrate_index=False,
    )

    store = get_global_repository(library_root)
    assert result.scanned is True
    assert result.rows == [{"rel": "a.jpg", "id": "asset-a"}]
    assert [row["rel"] for row in store.read_all(filter_hidden=False)] == ["a.jpg"]


def test_prepare_album_open_autoscan_prunes_stale_hidden_rows(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)
    store.write_rows(
        [
            {
                "rel": "album/deleted.mov",
                "id": "deleted-motion",
                "live_role": 1,
                "live_partner_rel": "album/deleted.heic",
            },
        ]
    )
    service = LibraryScanService(library_root, scanner=_Scanner([]))

    result = service.prepare_album_open(
        album_root,
        autoscan=True,
        hydrate_index=False,
    )

    assert result.scanned is True
    assert result.rows == []
    assert list(store.read_all(filter_hidden=False)) == []


def test_rescan_album_materializes_one_shot_filters_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()

    class _CapturingScanner:
        def __init__(self) -> None:
            self.include: list[str] | None = None
            self.exclude: list[str] | None = None

        def scan(
            self,
            _root: Path,
            include: Iterable[str],
            exclude: Iterable[str],
            **_kwargs: object,
        ):
            self.include = list(include)
            self.exclude = list(exclude)
            yield {"rel": "kept.jpg", "id": "kept"}

    scanner = _CapturingScanner()
    service = LibraryScanService(library_root, scanner=scanner)
    finalized: dict[str, object] = {}

    def fake_finalize_scan_result(
        _root: Path,
        rows: Iterable[dict[str, Any]],
        *,
        pair_live: bool = True,
        exclude: Iterable[str] | None = None,
        **_kwargs: object,
    ) -> list[dict[str, Any]]:
        finalized["pair_live"] = pair_live
        finalized["exclude"] = list(exclude or ())
        return [dict(row) for row in rows]

    monkeypatch.setattr(service, "finalize_scan_result", fake_finalize_scan_result)

    rows = service.rescan_album(
        library_root,
        include=(pattern for pattern in ("**/*.jpg",)),
        exclude=(pattern for pattern in ("**/.Trash/**",)),
        pair_live=False,
    )

    assert rows == [{"rel": "kept.jpg", "id": "kept"}]
    assert scanner.include == ["**/*.jpg"]
    assert scanner.exclude == ["**/.Trash/**"]
    assert finalized == {
        "pair_live": False,
        "exclude": ["**/.Trash/**"],
    }


def test_sync_manifest_favorites_raises_recoverable_errors_by_default(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    repository = _FavoriteFailingRepository()
    service = LibraryScanService(
        library_root,
        repository_factory=lambda _root: repository,
    )

    with pytest.raises(sqlite3.Error, match="db locked"):
        service.sync_manifest_favorites(library_root)

    assert repository.calls == 1


def test_sync_manifest_favorites_can_suppress_recoverable_open_errors(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    repository = _FavoriteFailingRepository()
    service = LibraryScanService(
        library_root,
        repository_factory=lambda _root: repository,
    )

    with caplog.at_level("WARNING"):
        service.sync_manifest_favorites(library_root, suppress_recoverable=True)

    assert repository.calls == 1
    assert "sync_favorites failed" in caplog.text


def test_scan_specific_files_prefixes_subalbum_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    asset = album_root / "a.jpg"
    asset.write_bytes(b"data")

    def fake_process_media_paths(root: Path, image_paths, video_paths, **_kwargs):
        assert root == album_root
        assert image_paths == [asset]
        assert video_paths == []
        return [{"rel": "a.jpg", "id": "asset-a"}]

    monkeypatch.setattr(
        scan_service_module,
        "process_media_paths",
        fake_process_media_paths,
    )

    service = LibraryScanService(library_root)
    rows = service.scan_specific_files(album_root, [asset])

    store = get_global_repository(library_root)
    assert rows == [{"rel": "album/a.jpg", "id": "asset-a"}]
    assert [row["rel"] for row in store.read_album_assets("album")] == [
        "album/a.jpg"
    ]


def test_scan_batch_committed_transport_contains_only_ready_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    rows = [
        {
            "rel": "ready.jpg",
            "id": "ready",
            "thumbnail_state": "ready",
            "micro_thumbnail": b"thumb",
            "thumb_cache_key": "thumb-ready",
        },
        {
            "rel": "failed.jpg",
            "id": "failed",
            "thumbnail_state": "failed",
            "thumb_error": "boom",
        },
    ]
    batches = []
    service = LibraryScanService(library_root, scanner=_Scanner(rows))

    result = service.scan_album(
        library_root,
        persist_chunks=True,
        chunk_size=2,
        scan_batch_callback=batches.append,
    )

    assert result.scan_job_id
    assert len(batches) == 1
    assert batches[0].job_id == result.scan_job_id
    assert [row["rel"] for row in batches[0].rows] == ["ready.jpg"]
    assert "db_commit" in batches[0].stage_elapsed_ms
    assert "discover" in batches[0].stage_elapsed_ms
    assert "stat_cache_validation" in batches[0].stage_elapsed_ms
    assert "metadata_extraction" in batches[0].stage_elapsed_ms

    with sqlite3.connect(get_global_repository(library_root).path) as conn:
        event = conn.execute(
            "SELECT event_type, payload_json FROM scan_events WHERE job_id = ? AND event_type = 'batch_committed'",
            [result.scan_job_id],
        ).fetchone()
        visible_event = conn.execute(
            "SELECT payload_json FROM scan_events WHERE job_id = ? AND event_type = 'stage_changed' AND payload_json LIKE ?",
            [result.scan_job_id, '%"visible_publish"%'],
        ).fetchone()
    assert event is not None
    assert event[0] == "batch_committed"
    payload = json.loads(event[1])
    assert payload["ready_rows"] == 1
    assert "stage_elapsed_ms" in payload
    assert "db_commit" in payload["stage_elapsed_ms"]
    assert "metadata_extraction" in payload["stage_elapsed_ms"]
    assert visible_event is not None
    visible_payload = json.loads(visible_event[0])
    assert "visible_publish" in visible_payload["stage_elapsed_ms"]
