from __future__ import annotations

import sqlite3
import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

import iPhoto.bootstrap.library_scan_service as scan_service_module
from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.cache.index_store import get_global_repository, reset_global_repository


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
