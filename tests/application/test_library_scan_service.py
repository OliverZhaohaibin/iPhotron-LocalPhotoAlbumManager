from __future__ import annotations

import sqlite3
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
