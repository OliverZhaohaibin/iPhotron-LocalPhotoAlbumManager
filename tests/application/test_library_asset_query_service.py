from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from iPhoto.bootstrap.library_asset_query_service import LibraryAssetQueryService
from iPhoto.cache.index_store import IndexStore, get_global_repository
from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.domain.models.core import MediaType
from iPhoto.domain.models.query import AssetQuery, ThumbnailReadyResult, ThumbnailState
from iPhoto.infrastructure.services.thumbnail_artifact import thumbnail_revision


class _Repository:
    def __init__(self) -> None:
        self.count_calls: list[dict[str, Any]] = []
        self.geometry_calls: list[dict[str, Any]] = []
        self.album_read_calls: list[dict[str, Any]] = []
        self.location_updates: list[tuple[str, str]] = []
        self.geometry_rows = [{"rel": "Trip/a.jpg", "id": "a"}]
        self.album_rows = [{"rel": "Trip/a.jpg", "id": "a"}]
        self.all_rows = [{"rel": "root.jpg", "id": "root"}]
        self.geotagged_rows = [{"rel": "Trip/a.jpg", "gps": {"lat": 1, "lon": 2}}]
        self.rows_by_rel = {"Trip/a.jpg": {"rel": "Trip/a.jpg", "is_favorite": 1}}
        self.rows_by_id = {
            "a": {"rel": "Trip/a.jpg", "id": "a", "dt": "2024-02-02T00:00:00", "live_role": 0},
            "b": {"rel": "Trip/b.jpg", "id": "b", "dt": "2024-02-03T00:00:00", "live_role": 0},
        }

    def count(self, **kwargs):
        self.count_calls.append(dict(kwargs))
        return 7

    def read_geometry_only(self, **kwargs):
        self.geometry_calls.append(dict(kwargs))
        return list(self.geometry_rows)

    def read_album_assets(self, album_path: str, **kwargs):
        call = dict(kwargs)
        call["album_path"] = album_path
        self.album_read_calls.append(call)
        return list(self.album_rows)

    def read_all(self, **_kwargs):
        return list(self.all_rows)

    def read_geotagged(self):
        return list(self.geotagged_rows)

    def update_location(self, rel: str, location: str) -> None:
        self.location_updates.append((rel, location))

    def get_rows_by_rels(self, rels):
        return {rel: self.rows_by_rel[rel] for rel in rels if rel in self.rows_by_rel}

    def get_rows_by_ids(self, asset_ids):
        return {
            asset_id: self.rows_by_id[asset_id]
            for asset_id in asset_ids
            if asset_id in self.rows_by_id
        }


class _BackfillRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.ready_updates: list[tuple[str, dict[str, Any]]] = []
        self.accept_ready_updates = True
        self.candidate_calls: list[tuple[Any, int, int]] = []
        self.backfill_candidates = [
            {"rel": "Trip/stale.jpg", "id": "stale", "thumbnail_state": "stale"}
        ]

    def read_thumbnail_backfill_candidates(self, query, first: int, limit: int):
        self.candidate_calls.append((query, first, limit))
        return list(self.backfill_candidates)

    def update_thumbnail_ready(self, rel: str, **kwargs: Any) -> bool:
        self.ready_updates.append((rel, kwargs))
        return self.accept_ready_updates


class _DeferredExecutor:
    def __init__(self) -> None:
        self.submitted = []
        self.shutdown_called = False

    def submit(self, fn, *args):
        self.submitted.append((fn, args))
        return None

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        del wait, cancel_futures
        self.shutdown_called = True


def test_count_and_geometry_rows_are_scoped_to_album_path(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    assert service.count_assets(album_root, filter_params={"filter_mode": "images"}) == 7
    rows = list(service.read_geometry_rows(album_root, filter_params={"filter_mode": "images"}))

    assert repo.count_calls == [
        {
            "filter_hidden": True,
            "filter_params": {"filter_mode": "images"},
            "album_path": "Trip",
            "include_subalbums": True,
        }
    ]
    assert repo.geometry_calls == [
        {
            "filter_params": {"filter_mode": "images"},
            "sort_by_date": True,
            "album_path": "Trip",
            "include_subalbums": True,
        }
    ]
    assert rows == [{"rel": "a.jpg", "id": "a"}]


def test_scoped_location_writer_maps_to_library_relative_path(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    service.location_cache_writer(album_root).update_location("a.jpg", "Paris")
    service.location_cache_writer(library_root).update_location("root.jpg", "Berlin")

    assert repo.location_updates == [
        ("Trip/a.jpg", "Paris"),
        ("root.jpg", "Berlin"),
    ]


def test_read_asset_and_geotagged_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    assert list(service.read_asset_rows(album_root)) == [{"rel": "a.jpg", "id": "a"}]
    assert list(service.read_library_relative_asset_rows(album_root)) == [
        {"rel": "Trip/a.jpg", "id": "a"}
    ]
    assert list(service.read_asset_rows(library_root)) == [
        {"rel": "root.jpg", "id": "root"}
    ]
    assert list(service.read_geotagged_rows()) == [
        {"rel": "Trip/a.jpg", "gps": {"lat": 1, "lon": 2}}
    ]


def test_favorite_status_for_path_uses_library_relative_rel(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    assert service.favorite_status_for_path(album_root / "a.jpg") is True
    assert service.favorite_status_for_path(album_root / "missing.jpg") is None


def test_count_query_assets_maps_simple_query_to_repository_filters(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    query = AssetQuery(is_favorite=True, media_types=[MediaType.VIDEO])

    assert service.count_query_assets(query) == 7
    assert repo.count_calls == [
        {
            "filter_hidden": True,
            "filter_params": {
                "media_type": 1,
                "filter_mode": "favorites",
                "exclude_path_prefix": RECENTLY_DELETED_DIR_NAME,
            },
            "album_path": None,
            "include_subalbums": False,
        }
    ]


def test_read_query_asset_rows_scopes_album_rows_and_applies_paging(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _Repository()
    repo.album_rows = [
        {"rel": "Trip/a.jpg", "id": "a", "live_role": 0},
        {"rel": "Trip/b.jpg", "id": "b", "live_role": 0},
    ]
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)
    query = AssetQuery(album_path="Trip", include_subalbums=True, offset=1, limit=1)

    rows = list(service.read_query_asset_rows(album_root, query))

    assert rows == [{"rel": "b.jpg", "id": "b", "live_role": 0}]
    assert repo.album_read_calls == [
        {
            "include_subalbums": True,
            "sort_by_date": True,
            "filter_hidden": True,
            "filter_params": {
                "exclude_path_prefix": RECENTLY_DELETED_DIR_NAME,
            },
            "album_path": "Trip",
        }
    ]


def test_asset_id_query_uses_rows_by_id_and_keeps_library_relative_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    repo = _Repository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    rows = list(
        service.read_query_asset_rows(
            library_root,
            AssetQuery(asset_ids=["a", "b"]),
        )
    )

    assert [row["id"] for row in rows] == ["b", "a"]
    assert [row["rel"] for row in rows] == ["Trip/b.jpg", "Trip/a.jpg"]


def test_album_id_query_filters_in_memory_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    repo = _Repository()
    repo.all_rows = [
        {"rel": "a.jpg", "id": "a", "album_id": "album-a", "live_role": 0},
        {"rel": "b.jpg", "id": "b", "album_id": "album-b", "live_role": 0},
        {"rel": "missing.jpg", "id": "missing", "live_role": 0},
    ]
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)
    query = AssetQuery(album_id="album-a")

    rows = list(service.read_query_asset_rows(library_root, query))

    assert rows == [{"rel": "a.jpg", "id": "a", "album_id": "album-a", "live_role": 0}]
    assert service.count_query_assets(query) == 1


def test_date_query_compares_scanned_utc_rows_with_naive_bounds(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    repo = _Repository()
    repo.all_rows = [
        {"rel": "before.jpg", "id": "before", "dt": "2024-01-01T09:59:59Z", "live_role": 0},
        {"rel": "inside.jpg", "id": "inside", "dt": "2024-01-01T10:30:00Z", "live_role": 0},
        {"rel": "after.jpg", "id": "after", "dt": "2024-01-01T11:00:01Z", "live_role": 0},
    ]
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    rows = list(
        service.read_query_asset_rows(
            library_root,
            AssetQuery(
                date_from=datetime(2024, 1, 1, 10, 0, 0),
                date_to=datetime(2024, 1, 1, 11, 0, 0),
            ),
        )
    )

    assert [row["id"] for row in rows] == ["inside"]


def test_count_query_assets_keeps_unrepresented_live_date_filters_in_memory(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    repo = _Repository()
    repo.all_rows = [
        {
            "rel": "before.jpg",
            "id": "before",
            "dt": "2024-01-01T09:59:59Z",
            "live_role": 0,
            "live_partner_rel": "before.mov",
        },
        {
            "rel": "inside.jpg",
            "id": "inside",
            "dt": "2024-01-01T10:30:00Z",
            "live_role": 0,
            "live_partner_rel": "inside.mov",
        },
        {
            "rel": "after.jpg",
            "id": "after",
            "dt": "2024-01-01T11:00:01Z",
            "live_role": 0,
            "live_partner_rel": "after.mov",
        },
    ]
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    count = service.count_query_assets(
        AssetQuery(
            media_types=[MediaType.LIVE_PHOTO],
            date_from=datetime(2024, 1, 1, 10, 0, 0),
            date_to=datetime(2024, 1, 1, 11, 0, 0),
        )
    )

    assert count == 1
    assert repo.count_calls == []


def test_query_asset_window_hides_non_ready_thumbnail_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    repo = IndexStore(library_root)
    base = datetime(2024, 1, 1)
    repo.write_rows(
        [
            {
                "rel": "stale.jpg",
                "id": "stale",
                "dt": "2024-01-01T00:00:03",
                "ts": int(base.timestamp() * 1_000_000) + 3,
                "media_type": 0,
                "thumbnail_state": "stale",
            },
            {
                "rel": "failed.jpg",
                "id": "failed",
                "dt": "2024-01-01T00:00:02",
                "ts": int(base.timestamp() * 1_000_000) + 2,
                "media_type": 0,
                "thumbnail_state": "failed",
            },
            {
                "rel": "ready.jpg",
                "id": "ready",
                "dt": "2024-01-01T00:00:01",
                "ts": int(base.timestamp() * 1_000_000) + 1,
                "media_type": 0,
                "thumbnail_state": "ready",
                "thumb_cache_key": "thumb-ready",
            },
        ]
    )
    service = LibraryAssetQueryService(
        library_root,
        repository_factory=lambda _root: repo,
    )

    window = service.read_query_asset_window(library_root, AssetQuery(), 0, 10)

    assert window.total_count == 1
    assert [row["id"] for row in window.rows] == ["ready"]


def test_thumbnail_hint_window_returns_only_existing_l2_identity(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    repo = IndexStore(library_root)
    repo.write_rows(
        [
            {
                "rel": "ready.jpg",
                "id": "ready",
                "media_type": 0,
                "thumbnail_state": "ready",
                "micro_thumbnail": b"micro",
                "thumb_cache_key": "thumb-ready",
            }
        ]
    )
    service = LibraryAssetQueryService(
        library_root,
        repository_factory=lambda _root: repo,
    )

    window = service.read_thumbnail_hint_window(library_root, AssetQuery(), 0, 10)

    assert window.total_count == -1
    assert window.rows == [
        {
            "rel": "ready.jpg",
            "thumb_cache_key": "thumb-ready",
            "thumbnail_state": "ready",
            "thumb_revision": None,
        }
    ]


def test_recently_deleted_query_includes_non_ready_thumbnail_rows(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    repo = IndexStore(library_root)
    base = datetime(2024, 1, 1)
    repo.write_rows(
        [
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/failed.jpg",
                "id": "failed",
                "dt": "2024-01-01T00:00:02",
                "ts": int(base.timestamp() * 1_000_000) + 2,
                "parent_album_path": RECENTLY_DELETED_DIR_NAME,
                "media_type": 0,
                "is_deleted": 1,
                "thumbnail_state": "failed",
            },
            {
                "rel": f"{RECENTLY_DELETED_DIR_NAME}/stale.jpg",
                "id": "stale",
                "dt": "2024-01-01T00:00:01",
                "ts": int(base.timestamp() * 1_000_000) + 1,
                "parent_album_path": RECENTLY_DELETED_DIR_NAME,
                "media_type": 0,
                "is_deleted": 1,
                "thumbnail_state": "stale",
            },
        ]
    )
    service = LibraryAssetQueryService(
        library_root,
        repository_factory=lambda _root: repo,
    )
    query = AssetQuery(album_path=RECENTLY_DELETED_DIR_NAME)

    window = service.read_query_asset_window(library_root, query, 0, 10)

    assert service.count_query_assets(query) == 2
    assert [row["id"] for row in window.rows] == ["failed", "stale"]


def test_thumbnail_backfill_request_is_deferred_off_call_path(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _BackfillRepository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)
    executor = _DeferredExecutor()
    service._thumbnail_backfill_executor = executor  # type: ignore[assignment]
    progress: list[tuple[Path, int, int]] = []
    service.thumbnail_backfill_progress.connect(
        lambda root, current, total: progress.append((root, current, total))
    )

    queued = service.request_thumbnail_backfill(album_root, AssetQuery(), 0, 100)

    assert queued == 1
    assert len(executor.submitted) == 1
    assert repo.ready_updates == []
    assert repo.candidate_calls == []
    assert service.thumbnail_backfill_pending() is True
    assert progress == []


def test_thumbnail_backfill_can_retry_after_submission_error(tmp_path: Path) -> None:
    repo = _BackfillRepository()
    service = LibraryAssetQueryService(tmp_path, repository_factory=lambda _root: repo)
    executor = _DeferredExecutor()
    service._thumbnail_backfill_executor = executor  # type: ignore[assignment]
    with patch.object(executor, "submit", side_effect=RuntimeError("cannot schedule")):
        with pytest.raises(RuntimeError, match="cannot schedule"):
            service.request_thumbnail_backfill(tmp_path, AssetQuery(), 0, 10)

    assert not service.thumbnail_backfill_pending()
    assert service.request_thumbnail_backfill(tmp_path, AssetQuery(), 0, 10) == 1
    service.shutdown()


def test_thumbnail_backfill_completion_publishes_ready_batch(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    (album_root / "stale.jpg").write_bytes(b"image")
    repo = _BackfillRepository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)
    executor = _DeferredExecutor()
    service._thumbnail_backfill_executor = executor  # type: ignore[assignment]
    batches = []
    progress: list[tuple[Path, int, int]] = []
    service.thumbnail_backfill_completed.connect(batches.append)
    service.thumbnail_backfill_progress.connect(
        lambda root, current, total: progress.append((root, current, total))
    )

    with patch(
        "iPhoto.bootstrap.library_asset_query_service.ensure_scan_thumbnail",
        return_value=SimpleNamespace(
            micro_thumbnail=b"micro",
            thumb_cache_key="thumb-key",
            thumb_error=None,
        ),
    ):
        assert service.request_thumbnail_backfill(album_root, AssetQuery(), 0, 100) == 1
        fn, args = executor.submitted[0]
        fn(*args)

    assert repo.ready_updates == [
        (
            "Trip/stale.jpg",
            {
                "micro_thumbnail": b"micro",
                "thumb_cache_key": "thumb-key",
                "expected_revision": None,
                "expected_asset_id": "stale",
                "expected_index_revision": None,
            },
        )
    ]
    assert len(batches) == 1
    batch = batches[0]
    assert batch.job_id.startswith("thumbnail-backfill:")
    assert batch.root == album_root
    assert batch.ready_count == 1
    assert batch.rows[0]["rel"] == "stale.jpg"
    assert batch.rows[0]["thumbnail_state"] == "ready"
    assert service.thumbnail_backfill_pending() is False
    assert progress == [(album_root, 0, 1), (album_root, 1, 1)]


def test_thumbnail_backfill_completion_releases_request_before_notify(tmp_path: Path) -> None:
    repo = _BackfillRepository()
    repo.backfill_candidates.clear()
    service = LibraryAssetQueryService(tmp_path, repository_factory=lambda _root: repo)
    executor = _DeferredExecutor()
    service._thumbnail_backfill_executor = executor  # type: ignore[assignment]
    query = AssetQuery()
    requeued = []
    service.thumbnail_backfill_completed.connect(
        lambda _batch: requeued.append(service.request_thumbnail_backfill(tmp_path, query, 0, 10)),
    )
    assert service.request_thumbnail_backfill(tmp_path, query, 0, 10) == 1
    run, args = executor.submitted[0]
    run(*args)

    assert requeued == [1]
    assert service.thumbnail_backfill_pending()
    service.shutdown()


def test_thumbnail_backfill_failed_rows_publish_empty_completion_batch(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _BackfillRepository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)
    executor = _DeferredExecutor()
    service._thumbnail_backfill_executor = executor  # type: ignore[assignment]
    batches = []
    service.thumbnail_backfill_completed.connect(batches.append)

    with patch(
        "iPhoto.bootstrap.library_asset_query_service.ensure_scan_thumbnail",
        return_value=SimpleNamespace(
            micro_thumbnail=None,
            thumb_cache_key=None,
            thumb_error="decode failed",
        ),
    ):
        assert service.request_thumbnail_backfill(album_root, AssetQuery(), 0, 100) == 1
        fn, args = executor.submitted[0]
        fn(*args)

    assert repo.ready_updates == [("Trip/stale.jpg", {
        "error": "decode failed", "expected_revision": None, "expected_asset_id": "stale",
        "expected_index_revision": None,
    })]
    assert len(batches) == 1
    assert batches[0].ready_count == 0
    assert batches[0].rows == []
    assert service.thumbnail_backfill_pending() is False


def test_thumbnail_backfill_exception_emits_failed_terminal_event(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _BackfillRepository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)
    executor = _DeferredExecutor()
    service._thumbnail_backfill_executor = executor  # type: ignore[assignment]
    failures: list[tuple[Path, str]] = []
    batches = []
    service.thumbnail_backfill_failed.connect(
        lambda root, error: failures.append((root, error))
    )
    service.thumbnail_backfill_completed.connect(batches.append)

    with patch(
        "iPhoto.bootstrap.library_asset_query_service.ensure_scan_thumbnail",
        side_effect=RuntimeError("thumbnail decode crashed"),
    ):
        assert service.request_thumbnail_backfill(album_root, AssetQuery(), 0, 100) == 1
        fn, args = executor.submitted[0]
        with pytest.raises(RuntimeError, match="thumbnail decode crashed"):
            fn(*args)

    assert failures == [(album_root, "thumbnail decode crashed")]
    assert batches == []
    assert service.thumbnail_backfill_pending() is False


def test_backfill_discovery_failure_has_matching_start_and_terminal_events(tmp_path: Path) -> None:
    repo = _BackfillRepository()
    service = LibraryAssetQueryService(tmp_path, repository_factory=lambda _root: repo)
    executor = _DeferredExecutor()
    service._thumbnail_backfill_executor = executor  # type: ignore[assignment]
    events = []
    service.thumbnail_backfill_progress.connect(lambda *args: events.append(args))
    service.thumbnail_backfill_failed.connect(lambda *args: events.append(args))
    assert service.request_thumbnail_backfill(tmp_path, AssetQuery(), 0, 10) == 1
    run, args = executor.submitted[0]

    with patch.object(repo, "read_thumbnail_backfill_candidates", side_effect=RuntimeError("discovery failed")):
        with pytest.raises(RuntimeError, match="discovery failed"):
            run(*args)

    assert events == [(tmp_path, 0, 0), (tmp_path, "discovery failed")]
    assert not service.thumbnail_backfill_pending()
    service.shutdown()


def test_thumbnail_backfill_does_not_emit_ready_row_when_revision_cas_rejects(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _BackfillRepository()
    repo.accept_ready_updates = False
    service = LibraryAssetQueryService(
        library_root,
        repository_factory=lambda _root: repo,
    )
    executor = _DeferredExecutor()
    service._thumbnail_backfill_executor = executor  # type: ignore[assignment]
    batches = []
    service.thumbnail_backfill_completed.connect(batches.append)

    with patch(
        "iPhoto.bootstrap.library_asset_query_service.ensure_scan_thumbnail",
        return_value=SimpleNamespace(
            micro_thumbnail=b"old-result",
            thumb_cache_key="stable-key",
            thumb_revision="revision-1",
            thumb_error=None,
        ),
    ):
        assert service.request_thumbnail_backfill(album_root, AssetQuery(), 0, 100) == 1
        run, args = executor.submitted[0]
        run(*args)

    assert len(batches) == 1
    assert batches[0].ready_count == 0
    assert batches[0].rows == []


@pytest.mark.parametrize("late_error", [False, True], ids=["success-after-replacement", "error-after-success"])
def test_legacy_thumbnail_backfill_does_not_overwrite_newer_row(
    tmp_path: Path, late_error: bool,
) -> None:
    repo = get_global_repository(tmp_path)
    repo.write_rows([{"rel": "a.jpg", "id": "old", "thumbnail_state": "stale"}])
    service = LibraryAssetQueryService(tmp_path, repository_factory=lambda _root: repo)
    executor = _DeferredExecutor()
    service._thumbnail_backfill_executor = executor  # type: ignore[assignment]
    batches = []
    service.thumbnail_backfill_completed.connect(batches.append)

    def finish_after_other_writer(*_args, **_kwargs):
        if late_error:
            assert repo.update_thumbnail_ready("a.jpg", thumb_cache_key="new-key")
            return SimpleNamespace(thumb_error="old decode failed", thumb_revision=None)
        repo.append_rows([{"rel": "a.jpg", "id": "new", "thumbnail_state": "stale"}])
        return SimpleNamespace(
            micro_thumbnail=b"old micro", thumb_cache_key="old-key",
            thumb_revision="old-revision", thumb_error=None,
        )

    with patch(
        "iPhoto.bootstrap.library_asset_query_service.ensure_scan_thumbnail",
        side_effect=finish_after_other_writer,
    ):
        assert service.request_thumbnail_backfill(tmp_path, AssetQuery(), 0, 10) == 1
        run, args = executor.submitted[0]
        run(*args)

    row = repo.get_rows_by_rels(["a.jpg"])["a.jpg"]
    assert len(batches) == 1
    assert batches[0].rows == []
    assert row["thumb_error"] is None
    assert row["id"] == ("old" if late_error else "new")
    assert row["thumb_cache_key"] == ("new-key" if late_error else None)
    assert row["thumbnail_state"] == ("ready" if late_error else "stale")
    service.shutdown()


@pytest.mark.parametrize(
    ("legacy_revision", "replace_source"),
    [(False, False), (True, False), (True, True)],
    ids=["known-revision", "legacy-revision", "changed-source"],
)
def test_thumbnail_backfill_success_after_concurrent_failure(
    tmp_path: Path,
    legacy_revision: bool,
    replace_source: bool,
) -> None:
    rel = "a.jpg"
    asset_path = tmp_path / rel
    asset_path.write_bytes(b"original media")
    revision = thumbnail_revision(asset_path)
    repo = get_global_repository(tmp_path)
    repo.write_rows([{
        "rel": rel, "id": "a", "thumbnail_state": "stale",
        "thumb_revision": None if legacy_revision else revision,
    }])
    successful = LibraryAssetQueryService(tmp_path)
    failing = LibraryAssetQueryService(tmp_path)
    success_executor, failure_executor = _DeferredExecutor(), _DeferredExecutor()
    successful._thumbnail_backfill_executor = success_executor  # type: ignore[assignment]
    failing._thumbnail_backfill_executor = failure_executor  # type: ignore[assignment]
    batches = []
    successful.thumbnail_backfill_completed.connect(batches.append)
    query = AssetQuery()
    render_target = "iPhoto.bootstrap.library_asset_query_service.ensure_scan_thumbnail"

    def finish_after_failed_attempt(*_args, **_kwargs) -> ThumbnailReadyResult:
        failed = ThumbnailReadyResult(
            ThumbnailState.FAILED, thumb_error="temporary decode failure", thumb_revision=revision,
        )
        with patch(render_target, return_value=failed):
            run, args = failure_executor.submitted[0]
            run(*args)
        assert repo.get_rows_by_rels([rel])[rel]["thumbnail_state"] == "failed"
        if replace_source:
            asset_path.write_bytes(b"replacement with different size")
        return ThumbnailReadyResult(
            ThumbnailState.READY, micro_thumbnail=b"micro", thumb_cache_key="ready-key",
            thumb_revision=revision,
        )

    try:
        assert successful.request_thumbnail_backfill(tmp_path, query, 0, 10) == 1
        assert failing.request_thumbnail_backfill(tmp_path, query, 0, 10) == 1
        with patch(render_target, side_effect=finish_after_failed_attempt):
            run, args = success_executor.submitted[0]
            run(*args)

        [batch] = batches
        row = repo.get_rows_by_rels([rel])[rel]
        if replace_source:
            assert batch.rows == []
            assert row["thumbnail_state"] == "failed"
            assert row["thumb_cache_key"] is None
        else:
            [published] = batch.rows
            assert published["thumb_revision"] == row["thumb_revision"] == revision
            assert row["thumb_error"] is None
            assert row["thumb_cache_key"] == "ready-key"
            assert successful.read_gallery_asset_window(tmp_path, query, 0, 10).total_count == 1
    finally:
        successful.shutdown()
        failing.shutdown()


def test_thumbnail_backfill_shutdown_suppresses_completion_event(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _BackfillRepository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)
    executor = _DeferredExecutor()
    service._thumbnail_backfill_executor = executor  # type: ignore[assignment]
    batches = []
    service.thumbnail_backfill_completed.connect(batches.append)

    with patch(
        "iPhoto.bootstrap.library_asset_query_service.ensure_scan_thumbnail",
        return_value=SimpleNamespace(
            micro_thumbnail=b"micro",
            thumb_cache_key="thumb-key",
            thumb_error=None,
        ),
    ):
        assert service.request_thumbnail_backfill(album_root, AssetQuery(), 0, 100) == 1
        service.shutdown()
        fn, args = executor.submitted[0]
        fn(*args)

    assert batches == []


def test_thumbnail_backfill_request_after_shutdown_is_ignored(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    repo = _BackfillRepository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)
    service.shutdown()

    assert service.request_thumbnail_backfill(album_root, AssetQuery(), 0, 100) == 0


def test_thumbnail_backfill_skips_queries_that_collection_api_cannot_represent(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    repo = _BackfillRepository()
    service = LibraryAssetQueryService(library_root, repository_factory=lambda _root: repo)

    queued = service.request_thumbnail_backfill(
        library_root,
        AssetQuery(asset_ids=["only-this-asset"]),
        0,
        100,
    )

    assert queued == 0
    assert repo.candidate_calls == []
