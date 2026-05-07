from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import iPhoto.bootstrap.library_scan_service as scan_service_module
from iPhoto.application.use_cases.scan_models import (
    ScanCompletion,
    ScanMode,
    ScanPressureLevel,
    ScanProgressPhase,
    ScanScopeKind,
)
from iPhoto.bootstrap.library_scan_service import (
    LARGE_LIBRARY_CONSTRAINED_THRESHOLD,
    LARGE_SUBTREE_CONSTRAINED_THRESHOLD,
    LibraryScanService,
)
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

    def merge_scan_rows(self, rows, **_kwargs):
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


def test_pair_album_library_root_preserves_live_pairs_across_prefixes(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    service = LibraryScanService(library_root)
    rows = [
        {
            "rel": "album-a/IMG_0001.HEIC",
            "id": "still-a",
            "mime": "image/heic",
            "dt": "2024-01-01T00:00:00Z",
            "ts": 1,
            "bytes": 1,
            "content_id": "live-a",
        },
        {
            "rel": "album-a/IMG_0001.MOV",
            "id": "motion-a",
            "mime": "video/quicktime",
            "dt": "2024-01-01T00:00:00Z",
            "ts": 1,
            "bytes": 1,
            "content_id": "live-a",
        },
        {
            "rel": "album-b/IMG_0002.HEIC",
            "id": "still-b",
            "mime": "image/heic",
            "dt": "2024-01-02T00:00:00Z",
            "ts": 2,
            "bytes": 1,
            "content_id": "live-b",
        },
        {
            "rel": "album-b/IMG_0002.MOV",
            "id": "motion-b",
            "mime": "video/quicktime",
            "dt": "2024-01-02T00:00:00Z",
            "ts": 2,
            "bytes": 1,
            "content_id": "live-b",
        },
    ]

    service.finalize_scan(library_root, rows)
    groups = service.pair_album(library_root)

    store = get_global_repository(library_root)
    indexed = {
        row["rel"]: row
        for row in store.read_all(filter_hidden=False)
    }
    assert {(group.still, group.motion) for group in groups} == {
        ("album-a/IMG_0001.HEIC", "album-a/IMG_0001.MOV"),
        ("album-b/IMG_0002.HEIC", "album-b/IMG_0002.MOV"),
    }
    assert indexed["album-a/IMG_0001.HEIC"]["live_role"] == 0
    assert indexed["album-a/IMG_0001.HEIC"]["live_partner_rel"] == "album-a/IMG_0001.MOV"
    assert indexed["album-a/IMG_0001.MOV"]["live_role"] == 1
    assert indexed["album-a/IMG_0001.MOV"]["live_partner_rel"] == "album-a/IMG_0001.HEIC"
    assert indexed["album-b/IMG_0002.HEIC"]["live_role"] == 0
    assert indexed["album-b/IMG_0002.HEIC"]["live_partner_rel"] == "album-b/IMG_0002.MOV"
    assert indexed["album-b/IMG_0002.MOV"]["live_role"] == 1
    assert indexed["album-b/IMG_0002.MOV"]["live_partner_rel"] == "album-b/IMG_0002.HEIC"


def test_pair_album_large_scope_keeps_content_id_pairing_global(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    service = LibraryScanService(library_root)
    rows = [
        {
            "rel": "album-a/IMG_0001.HEIC",
            "id": "still",
            "mime": "image/heic",
            "dt": "2024-01-01T00:00:00Z",
            "ts": 1,
            "bytes": 1,
            "content_id": "live-cross",
        },
        {
            "rel": "album-b/IMG_0001.MOV",
            "id": "motion",
            "mime": "video/quicktime",
            "dt": "2024-01-01T00:00:00Z",
            "ts": 1,
            "bytes": 1,
            "content_id": "live-cross",
        },
    ]

    service.finalize_scan(library_root, rows)
    monkeypatch.setattr(
        service,
        "_should_pair_scope_directly",
        lambda _album_path, *, repository: False,
    )

    groups = service.pair_album(library_root)

    store = get_global_repository(library_root)
    indexed = {
        row["rel"]: row
        for row in store.read_all(filter_hidden=False)
    }
    assert {(group.still, group.motion) for group in groups} == {
        ("album-a/IMG_0001.HEIC", "album-b/IMG_0001.MOV"),
    }
    assert indexed["album-a/IMG_0001.HEIC"]["live_role"] == 0
    assert indexed["album-a/IMG_0001.HEIC"]["live_partner_rel"] == "album-b/IMG_0001.MOV"
    assert indexed["album-b/IMG_0001.MOV"]["live_role"] == 1
    assert indexed["album-b/IMG_0001.MOV"]["live_partner_rel"] == "album-a/IMG_0001.HEIC"


def test_pair_album_library_root_includes_legacy_null_prefix_rows(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    store = get_global_repository(library_root)
    with store.transaction() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO assets (
                rel, id, parent_album_path, mime, dt, ts, bytes, content_id
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                "IMG_0001.HEIC",
                "still",
                "image/heic",
                "2024-01-01T00:00:00Z",
                1,
                1,
                "live-a",
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO assets (
                rel, id, parent_album_path, mime, dt, ts, bytes, content_id
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                "IMG_0001.MOV",
                "motion",
                "video/quicktime",
                "2024-01-01T00:00:00Z",
                1,
                1,
                "live-a",
            ),
        )

    service = LibraryScanService(library_root)
    groups = service.pair_album(library_root)

    indexed = {
        row["rel"]: row
        for row in store.read_all(filter_hidden=False)
    }
    assert {(group.still, group.motion) for group in groups} == {
        ("IMG_0001.HEIC", "IMG_0001.MOV"),
    }
    assert indexed["IMG_0001.HEIC"]["live_role"] == 0
    assert indexed["IMG_0001.HEIC"]["live_partner_rel"] == "IMG_0001.MOV"
    assert indexed["IMG_0001.MOV"]["live_role"] == 1
    assert indexed["IMG_0001.MOV"]["live_partner_rel"] == "IMG_0001.HEIC"


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

    def fake_process_media_paths(root: Path, image_paths, video_paths):
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


def test_plan_scan_initial_safe_defers_face_scan_and_live_pairing(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    service = LibraryScanService(library_root, scanner=_Scanner([]))

    plan = service.plan_scan(library_root, mode=ScanMode.INITIAL_SAFE)

    assert plan.safe_mode is True
    assert plan.allow_face_scan is False
    assert plan.defer_live_pairing is True
    assert plan.persist_chunks is True
    assert plan.collect_rows is False
    assert plan.pressure_level == ScanPressureLevel.CONSTRAINED


def test_plan_scan_background_constrains_large_library_scope_estimate(
    tmp_path: Path,
) -> None:
    repository = _CountingRepository(LARGE_LIBRARY_CONSTRAINED_THRESHOLD)
    repository.library_root = tmp_path / "library"
    repository.library_root.mkdir()
    repository.path = repository.library_root / ".iPhoto" / "global_index.db"
    service = LibraryScanService(
        repository.library_root,
        scanner=_Scanner([]),
        repository_factory=lambda _root: repository,
    )

    plan = service.plan_scan(repository.library_root, mode=ScanMode.BACKGROUND)

    assert plan.scope_kind == ScanScopeKind.LIBRARY_ROOT
    assert plan.pressure_level == ScanPressureLevel.CONSTRAINED
    assert plan.generate_micro_thumbnails is False
    assert plan.allow_face_scan is False
    assert plan.defer_live_pairing is True
    assert plan.degrade_reason is not None


def test_plan_scan_repair_import_uses_library_threshold_for_library_root(
    tmp_path: Path,
) -> None:
    repository = _CountingRepository(LARGE_LIBRARY_CONSTRAINED_THRESHOLD)
    repository.library_root = tmp_path / "library"
    repository.library_root.mkdir()
    repository.path = repository.library_root / ".iPhoto" / "global_index.db"
    service = LibraryScanService(
        repository.library_root,
        scanner=_Scanner([]),
        repository_factory=lambda _root: repository,
    )

    plan = service.plan_scan(
        repository.library_root,
        mode=ScanMode.BACKGROUND,
        scope_kind=ScanScopeKind.REPAIR_IMPORT,
    )

    assert plan.pressure_level == ScanPressureLevel.CONSTRAINED
    assert plan.defer_live_pairing is True


def test_complete_scan_prunes_by_last_seen_scan_id(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)
    store.write_rows(
        [
            {
                "rel": "album/keep.jpg",
                "id": "keep",
                "last_seen_scan_id": "scan-1",
            },
            {
                "rel": "album/stale.jpg",
                "id": "stale",
                "last_seen_scan_id": "old-scan",
            },
        ]
    )
    service = LibraryScanService(library_root, scanner=_Scanner([]))
    store.create_scan_run(
        "scan-1",
        scope_root=album_root.resolve().as_posix(),
        mode=ScanMode.BACKGROUND.value,
        safe_mode=False,
        phase=ScanProgressPhase.INDEXING.value,
    )

    finalized = service.complete_scan(
        ScanCompletion(
            root=album_root,
            scan_id="scan-1",
            mode=ScanMode.BACKGROUND,
            processed_count=1,
            failed_count=0,
            success=True,
            cancelled=False,
            safe_mode=False,
            defer_live_pairing=True,
            allow_face_scan=True,
            phase=ScanProgressPhase.COMPLETED,
        ),
        pair_live=False,
    )

    rows = list(
        store.read_album_assets(
            "album",
            include_subalbums=True,
            filter_hidden=False,
        )
    )
    assert [row["rel"] for row in rows] == ["album/keep.jpg"]
    assert finalized.phase == ScanProgressPhase.DEFERRED_PAIRING
    assert service.has_incomplete_scan(album_root) is True


def test_resume_scan_promotes_deferred_pairing_runs_to_background(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)
    service = LibraryScanService(library_root, scanner=_Scanner([]))
    store.create_scan_run(
        "scan-1",
        scope_root=album_root.resolve().as_posix(),
        mode=ScanMode.INITIAL_SAFE.value,
        safe_mode=True,
        phase=ScanProgressPhase.DEFERRED_PAIRING.value,
    )
    store.update_scan_run(
        "scan-1",
        state="paused",
        phase=ScanProgressPhase.DEFERRED_PAIRING.value,
    )

    plan = service.resume_scan(album_root)

    assert plan.scan_id != "scan-1"
    assert plan.resumed_from_scan_id == "scan-1"
    assert plan.mode == ScanMode.BACKGROUND
    assert plan.defer_live_pairing is False
    assert plan.allow_face_scan is True


def test_resume_scan_inherits_constrained_pressure_and_deferred_tasks(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)
    service = LibraryScanService(library_root, scanner=_Scanner([]))
    store.create_scan_run(
        "scan-1",
        scope_root=album_root.resolve().as_posix(),
        mode=ScanMode.BACKGROUND.value,
        safe_mode=False,
        phase=ScanProgressPhase.INDEXING.value,
        pressure_level=ScanPressureLevel.CONSTRAINED.value,
        degrade_reason="memory warning",
        deferred_tasks="live_pairing,face_scan",
    )
    store.update_scan_run(
        "scan-1",
        state="paused",
        phase=ScanProgressPhase.CANCELLED_RESUMABLE.value,
        pressure_level=ScanPressureLevel.CONSTRAINED.value,
        degrade_reason="memory warning",
        deferred_tasks="live_pairing,face_scan",
    )

    resumed = service.resume_scan(album_root)

    assert resumed.pressure_level == ScanPressureLevel.CONSTRAINED
    assert resumed.allow_face_scan is False
    assert resumed.defer_live_pairing is True
    assert resumed.degrade_reason == "memory warning"


def test_resume_scan_reconstrains_legacy_initial_safe_runs(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)
    service = LibraryScanService(library_root, scanner=_Scanner([]))
    store.create_scan_run(
        "scan-1",
        scope_root=album_root.resolve().as_posix(),
        mode=ScanMode.INITIAL_SAFE.value,
        safe_mode=True,
        phase=ScanProgressPhase.INDEXING.value,
    )
    store.update_scan_run(
        "scan-1",
        state="paused",
        phase=ScanProgressPhase.CANCELLED_RESUMABLE.value,
    )

    resumed = service.resume_scan(album_root)

    assert resumed.mode == ScanMode.INITIAL_SAFE
    assert resumed.pressure_level == ScanPressureLevel.CONSTRAINED
    assert resumed.generate_micro_thumbnails is False
    assert resumed.allow_face_scan is False
    assert resumed.defer_live_pairing is True


def test_resumed_scan_uses_fresh_scan_id_for_pruning_and_clears_old_pause(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)
    store.write_rows(
        [
            {
                "rel": "album/keep.jpg",
                "id": "keep",
                "last_seen_scan_id": "scan-1",
            },
            {
                "rel": "album/stale.jpg",
                "id": "stale",
                "last_seen_scan_id": "scan-1",
            },
        ]
    )
    service = LibraryScanService(
        library_root,
        scanner=_Scanner([{"rel": "keep.jpg", "id": "keep"}]),
    )
    store.create_scan_run(
        "scan-1",
        scope_root=album_root.resolve().as_posix(),
        mode=ScanMode.BACKGROUND.value,
        safe_mode=False,
        phase=ScanProgressPhase.INDEXING.value,
    )
    store.update_scan_run(
        "scan-1",
        state="paused",
        phase=ScanProgressPhase.CANCELLED_RESUMABLE.value,
    )

    plan = service.resume_scan(album_root)
    result = service.start_scan(plan)
    service.complete_scan(
        ScanCompletion(
            root=album_root,
            scan_id=plan.scan_id,
            mode=plan.mode,
            processed_count=result.processed_count,
            failed_count=result.failed_count,
            success=True,
            cancelled=False,
            safe_mode=plan.safe_mode,
            defer_live_pairing=plan.defer_live_pairing,
            allow_face_scan=plan.allow_face_scan,
            phase=ScanProgressPhase.COMPLETED,
        ),
        pair_live=False,
    )

    rows = list(
        store.read_album_assets(
            "album",
            include_subalbums=True,
            filter_hidden=False,
        )
    )
    resumed_run = store.latest_incomplete_scan_run(
        scope_root=album_root.resolve().as_posix(),
    )

    assert plan.scan_id != "scan-1"
    assert plan.resumed_from_scan_id == "scan-1"
    assert [row["rel"] for row in rows] == ["album/keep.jpg"]
    assert rows[0]["last_seen_scan_id"] == plan.scan_id
    assert resumed_run is None
    assert service.has_incomplete_scan(album_root) is False


def test_cancelled_resumed_background_scan_resumes_as_background_again(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)
    service = LibraryScanService(library_root, scanner=_Scanner([]))
    store.create_scan_run(
        "scan-1",
        scope_root=album_root.resolve().as_posix(),
        mode=ScanMode.INITIAL_SAFE.value,
        safe_mode=True,
        phase=ScanProgressPhase.DEFERRED_PAIRING.value,
    )
    store.update_scan_run(
        "scan-1",
        state="paused",
        phase=ScanProgressPhase.DEFERRED_PAIRING.value,
    )

    first_resume = service.resume_scan(album_root)
    service.start_scan(first_resume)
    service.complete_scan(
        ScanCompletion(
            root=album_root,
            scan_id=first_resume.scan_id,
            mode=first_resume.mode,
            processed_count=0,
            failed_count=0,
            success=True,
            cancelled=True,
            safe_mode=first_resume.safe_mode,
            defer_live_pairing=first_resume.defer_live_pairing,
            allow_face_scan=first_resume.allow_face_scan,
            phase=ScanProgressPhase.CANCELLED_RESUMABLE,
        ),
        pair_live=False,
    )

    second_resume = service.resume_scan(album_root)

    assert first_resume.mode == ScanMode.BACKGROUND
    assert second_resume.mode == ScanMode.BACKGROUND
    assert second_resume.resumed_from_scan_id == first_resume.scan_id
    assert second_resume.scan_id != first_resume.scan_id


def test_complete_scan_keeps_freshly_persisted_rows_for_same_scan_id(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    service = LibraryScanService(
        library_root,
        scanner=_Scanner([{"rel": "fresh.jpg", "id": "fresh"}]),
    )
    plan = service.plan_scan(
        album_root,
        mode=ScanMode.BACKGROUND,
        persist_chunks=True,
        collect_rows=False,
        scan_id="scan-1",
    )

    result = service.start_scan(plan)
    finalized = service.complete_scan(
        ScanCompletion(
            root=album_root,
            scan_id=plan.scan_id,
            mode=plan.mode,
            processed_count=result.processed_count,
            failed_count=result.failed_count,
            success=True,
            cancelled=False,
            safe_mode=plan.safe_mode,
            defer_live_pairing=plan.defer_live_pairing,
            allow_face_scan=plan.allow_face_scan,
            phase=ScanProgressPhase.COMPLETED,
        ),
        pair_live=False,
    )

    store = get_global_repository(library_root)
    rows = list(
        store.read_album_assets(
            "album",
            include_subalbums=True,
            filter_hidden=False,
        )
    )
    assert [row["rel"] for row in rows] == ["album/fresh.jpg"]
    assert rows[0]["last_seen_scan_id"] == "scan-1"
    assert finalized.phase == ScanProgressPhase.COMPLETED


def test_start_scan_preserves_degraded_pressure_in_scan_run(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)

    class _ProgressScanner:
        def scan(
            self,
            _root: Path,
            _include: Iterable[str],
            _exclude: Iterable[str],
            **kwargs: object,
        ):
            progress_callback = kwargs.get("progress_callback")
            if callable(progress_callback):
                progress_callback(1, 1)
            yield {"rel": "fresh.jpg", "id": "fresh"}

    service = LibraryScanService(library_root, scanner=_ProgressScanner())
    plan = service.plan_scan(
        album_root,
        mode=ScanMode.BACKGROUND,
        persist_chunks=True,
        collect_rows=False,
    )
    current_plan = plan

    def progress_callback(_processed: int, total: int) -> None:
        nonlocal current_plan
        if total <= 0 or current_plan.pressure_level != ScanPressureLevel.NORMAL:
            return
        current_plan = replace(
            current_plan,
            pressure_level=ScanPressureLevel.CONSTRAINED,
            degrade_reason="memory warning",
            generate_micro_thumbnails=False,
            allow_face_scan=False,
            defer_live_pairing=True,
        )
        service.mark_scan_constrained(
            plan.scan_id,
            reason="memory warning",
            defer_live_pairing=True,
            defer_face_scan=True,
        )

    service.start_scan(
        plan,
        progress_callback=progress_callback,
        plan_state_resolver=lambda: current_plan,
    )

    run = store.latest_incomplete_scan_run(
        scope_root=album_root.resolve().as_posix(),
    )
    assert run is not None
    assert run["pressure_level"] == ScanPressureLevel.CONSTRAINED.value
    assert run["deferred_tasks"] == "live_pairing,face_scan"
    assert run["degrade_reason"] == "memory warning"


def test_complete_scan_constrained_background_defers_pairing(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)
    service = LibraryScanService(
        library_root,
        scanner=_Scanner([{"rel": "fresh.jpg", "id": "fresh"}]),
    )
    plan = service.plan_scan(
        album_root,
        mode=ScanMode.BACKGROUND,
        persist_chunks=True,
        collect_rows=False,
        pressure_level=ScanPressureLevel.CONSTRAINED,
        degrade_reason="memory warning",
        generate_micro_thumbnails=False,
        allow_face_scan=False,
        defer_live_pairing=True,
    )

    result = service.start_scan(plan)
    finalized = service.complete_scan(
        ScanCompletion(
            root=album_root,
            scan_id=plan.scan_id,
            mode=plan.mode,
            processed_count=result.processed_count,
            pressure_level=plan.pressure_level,
            failed_count=result.failed_count,
            success=True,
            cancelled=False,
            safe_mode=plan.safe_mode,
            defer_live_pairing=plan.defer_live_pairing,
            deferred_face_scan=True,
            degrade_reason=plan.degrade_reason,
            deferred_pairing_reason="memory warning",
            allow_face_scan=plan.allow_face_scan,
            phase=ScanProgressPhase.COMPLETED,
        ),
        pair_live=False,
    )

    run = store.latest_incomplete_scan_run(
        scope_root=album_root.resolve().as_posix(),
    )
    assert finalized.phase == ScanProgressPhase.DEFERRED_PAIRING
    assert finalized.pressure_level == ScanPressureLevel.CONSTRAINED
    assert finalized.deferred_pairing_reason == "memory warning"
    assert run is not None
    assert run["pressure_level"] == ScanPressureLevel.CONSTRAINED.value
    assert run["deferred_tasks"] == "live_pairing,face_scan"


def test_rescan_scope_bounded_marks_cancelled_runs_unsuccessful(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    service = LibraryScanService(
        library_root,
        scanner=_Scanner([{"rel": "fresh.jpg", "id": "fresh"}]),
    )

    completion = service.rescan_scope_bounded(
        album_root,
        scope_kind=ScanScopeKind.REPAIR_IMPORT,
        is_cancelled=lambda: True,
    )

    assert completion.success is False
    assert completion.cancelled is True
    assert completion.phase == ScanProgressPhase.CANCELLED_RESUMABLE


def test_rescan_scope_bounded_defers_pairing_for_constrained_repair_scans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    service = LibraryScanService(
        library_root,
        scanner=_Scanner([{"rel": "fresh.jpg", "id": "fresh"}]),
    )
    monkeypatch.setattr(
        service,
        "_estimate_scope_asset_count",
        lambda _root, *, scope_kind: LARGE_SUBTREE_CONSTRAINED_THRESHOLD,
    )

    completion = service.rescan_scope_bounded(
        album_root,
        pair_live=True,
        scope_kind=ScanScopeKind.REPAIR_IMPORT,
    )

    assert completion.success is True
    assert completion.pressure_level == ScanPressureLevel.CONSTRAINED
    assert completion.defer_live_pairing is True
    assert completion.phase == ScanProgressPhase.DEFERRED_PAIRING


def test_rescan_scope_bounded_degrades_when_discovery_exceeds_threshold(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    store = get_global_repository(library_root)

    class _ProgressScanner:
        def scan(
            self,
            _root: Path,
            _include: Iterable[str],
            _exclude: Iterable[str],
            **kwargs: object,
        ):
            progress_callback = kwargs.get("progress_callback")
            if callable(progress_callback):
                progress_callback(1, LARGE_SUBTREE_CONSTRAINED_THRESHOLD)
            yield {"rel": "fresh.jpg", "id": "fresh"}

    service = LibraryScanService(library_root, scanner=_ProgressScanner())

    completion = service.rescan_scope_bounded(
        album_root,
        pair_live=True,
        scope_kind=ScanScopeKind.REPAIR_IMPORT,
    )

    run = store.latest_incomplete_scan_run(
        scope_root=album_root.resolve().as_posix(),
    )

    assert completion.success is True
    assert completion.pressure_level == ScanPressureLevel.CONSTRAINED
    assert completion.degrade_reason == (
        f"large scope discovered: {LARGE_SUBTREE_CONSTRAINED_THRESHOLD} assets"
    )
    assert completion.phase == ScanProgressPhase.DEFERRED_PAIRING
    assert run is not None
    assert run["pressure_level"] == ScanPressureLevel.CONSTRAINED.value
    assert run["deferred_tasks"] == "live_pairing,face_scan"


def test_complete_scan_deferred_pairing_hides_motion_components(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "library"
    album_root = library_root / "album"
    album_root.mkdir(parents=True)
    service = LibraryScanService(
        library_root,
        scanner=_Scanner(
            [
                {
                    "rel": "IMG_0001.HEIC",
                    "id": "still",
                    "mime": "image/heic",
                    "dt": "2024-01-01T00:00:00Z",
                    "ts": 1,
                    "bytes": 1,
                    "content_id": "live-a",
                },
                {
                    "rel": "IMG_0001.MOV",
                    "id": "motion",
                    "mime": "video/quicktime",
                    "dt": "2024-01-01T00:00:00Z",
                    "ts": 1,
                    "bytes": 1,
                    "content_id": "live-a",
                },
            ]
        ),
    )
    plan = service.plan_scan(album_root, mode=ScanMode.INITIAL_SAFE)

    result = service.start_scan(plan)
    finalized = service.complete_scan(
        ScanCompletion(
            root=album_root,
            scan_id=plan.scan_id,
            mode=plan.mode,
            processed_count=result.processed_count,
            failed_count=result.failed_count,
            success=True,
            cancelled=False,
            safe_mode=plan.safe_mode,
            defer_live_pairing=plan.defer_live_pairing,
            allow_face_scan=plan.allow_face_scan,
            phase=ScanProgressPhase.COMPLETED,
        ),
        pair_live=False,
    )

    store = get_global_repository(library_root)
    visible_rows = list(
        store.read_album_assets(
            "album",
            include_subalbums=True,
            filter_hidden=True,
        )
    )
    all_rows = {
        row["rel"]: row
        for row in store.read_album_assets(
            "album",
            include_subalbums=True,
            filter_hidden=False,
        )
    }

    assert finalized.phase == ScanProgressPhase.DEFERRED_PAIRING
    assert [row["rel"] for row in visible_rows] == ["album/IMG_0001.HEIC"]
    assert all_rows["album/IMG_0001.HEIC"]["live_partner_rel"] == "album/IMG_0001.MOV"
    assert all_rows["album/IMG_0001.MOV"]["live_role"] == 1
