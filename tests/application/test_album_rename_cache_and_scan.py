from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
from PIL import Image
from PySide6.QtCore import QSize

from iPhoto.application.use_cases.scan_library import ScanLibraryUseCase
from iPhoto.bootstrap.library_asset_query_service import LibraryAssetQueryService
from iPhoto.bootstrap.library_scan_service import LibraryScanService
from iPhoto.cache.index_store import get_global_repository, reset_global_repository
from iPhoto.errors import AlbumOperationError
from iPhoto.infrastructure.services.thumbnail_artifact import (
    publish_thumbnail_artifact,
    thumbnail_revision,
)
from iPhoto.infrastructure.services.thumbnail_cache_keys import (
    thumbnail_cache_file,
    thumbnail_cache_file_for_key,
)
from iPhoto.infrastructure.services.thumbnail_cache_service import ThumbnailCacheService


@pytest.fixture(autouse=True)
def clean_repository():
    reset_global_repository()
    yield
    reset_global_repository()


def _indexed_photo(root: Path):
    source = root / "Old" / "Nested" / "photo.jpg"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (32, 32), "blue").save(source)
    cache = root / ".iPhoto" / "cache" / "thumbs"
    artifact = publish_thumbnail_artifact(
        source, cache, expected_revision=thumbnail_revision(source)
    )
    assert artifact is not None
    repository = get_global_repository(root)
    repository.write_rows([{
        "rel": "Old/Nested/photo.jpg", "id": "photo", "thumbnail_state": "ready",
        "thumb_cache_key": artifact.cache_key, "thumb_revision": artifact.revision,
        "micro_thumbnail": artifact.micro_thumbnail, "is_favorite": True,
    }])
    return source, cache, artifact, repository


@pytest.mark.parametrize("names", [("New",), ("New", "Final")])
def test_album_rename_moves_cached_pixels_without_rendering_and_releases_old_key(
    tmp_path: Path, qapp, names: tuple[str, ...]
) -> None:
    source, cache, artifact, repository = _indexed_photo(tmp_path)
    service = LibraryScanService(tmp_path)
    previous = tmp_path / "Old"
    with patch(
        "iPhoto.infrastructure.services.thumbnail_artifact.render_thumbnail_image",
        side_effect=AssertionError("rename must reuse the existing artifact"),
    ):
        for name in names:
            destination = tmp_path / name
            service.rename_album(previous, destination)
            previous = destination
    rel = f"{names[-1]}/Nested/photo.jpg"
    row = repository.get_rows_by_rels([rel])[rel]
    assert row["thumbnail_state"] == "ready"
    assert row["micro_thumbnail"] == artifact.micro_thumbnail
    assert row["is_favorite"]
    assert row["thumb_cache_key"] != artifact.cache_key
    assert not thumbnail_cache_file_for_key(cache, artifact.cache_key).exists()
    renamed_cache = thumbnail_cache_file_for_key(cache, row["thumb_cache_key"])
    with Image.open(renamed_cache) as image:
        assert image.getpixel((256, 256))[2] > 200

    source.parent.mkdir(parents=True)
    Image.new("RGB", (32, 32), "red").save(source)
    replacement = publish_thumbnail_artifact(
        source, cache, expected_revision=thumbnail_revision(source)
    )
    assert replacement is not None
    assert row["thumb_cache_key"] != replacement.cache_key
    with Image.open(renamed_cache) as image:
        assert image.getpixel((256, 256))[2] > 200


@pytest.mark.parametrize("cache_condition", ["present", "missing", "locked"])
def test_album_rename_cache_miss_regenerates_and_publishes_current_revision(
    tmp_path: Path, qapp, cache_condition: str
) -> None:
    _source, cache, artifact, repository = _indexed_photo(tmp_path)
    if cache_condition == "missing":
        for path in cache.glob("*.jpg"):
            path.unlink()
    # A previously removed album can leave unrelated pixels at the new path's
    # ordinary key. A missing/locked source must never make those pixels ready.
    destination = tmp_path / "New" / "Nested" / "photo.jpg"
    Image.new("RGB", (512, 512), "red").save(thumbnail_cache_file(cache, destination))
    original_replace = Path.replace

    def replace(path, target):
        if (
            cache_condition == "locked"
            and path == thumbnail_cache_file_for_key(cache, artifact.cache_key)
        ):
            raise PermissionError("cache file is temporarily locked")
        return original_replace(path, target)

    with patch.object(Path, "replace", replace):
        LibraryScanService(tmp_path).rename_album(tmp_path / "Old", tmp_path / "New")
    row = repository.get_rows_by_rels(["New/Nested/photo.jpg"])["New/Nested/photo.jpg"]
    if cache_condition == "present":
        for path in cache.glob("*.jpg"):
            path.unlink()
    source = tmp_path / "New" / "Nested" / "photo.jpg"
    thumbnails = ThumbnailCacheService(cache)
    thumbnails.set_thumbnail_state_service(LibraryAssetQueryService(tmp_path))
    try:
        image = thumbnails._load_or_render_thumbnail(
            source, QSize(512, 512), l2_cache_key=row["thumb_cache_key"],
            thumbnail_state=row["thumbnail_state"], thumb_revision=row["thumb_revision"],
        )
        assert image is not None
        assert image.pixelColor(256, 256).blue() > 200
        ready = repository.get_rows_by_rels([row["rel"]])[row["rel"]]
        assert ready["thumbnail_state"] == "ready"
        assert ready["thumb_revision"] == thumbnail_revision(source)
        assert thumbnail_cache_file_for_key(cache, ready["thumb_cache_key"]).is_file()
    finally:
        thumbnails.shutdown()


def test_failed_index_rename_keeps_original_cached_artifact(tmp_path: Path, qapp) -> None:
    source, cache, artifact, repository = _indexed_photo(tmp_path)
    original_cache = thumbnail_cache_file_for_key(cache, artifact.cache_key)
    cached_bytes = original_cache.read_bytes()
    repository.append_rows([{"rel": "New/Nested/photo.jpg", "id": "conflict"}])

    with pytest.raises(AlbumOperationError, match="already has indexed assets"):
        LibraryScanService(tmp_path).rename_album(tmp_path / "Old", tmp_path / "New")

    assert source.is_file()
    assert not (tmp_path / "New").exists()
    row = repository.get_rows_by_rels(["Old/Nested/photo.jpg"])["Old/Nested/photo.jpg"]
    assert row["thumb_cache_key"] == artifact.cache_key
    assert row["thumb_revision"] == artifact.revision
    assert original_cache.read_bytes() == cached_bytes


@pytest.mark.parametrize("recreate_old_path", [False, True])
def test_rename_rejects_scan_batch_that_passed_cancellation_check(
    tmp_path: Path, recreate_old_path: bool
) -> None:
    old = tmp_path / "Old"
    old.mkdir()
    row = {"rel": "Old/photo.jpg", "id": "photo", "thumbnail_state": "ready",
           "thumb_cache_key": "cached", "is_favorite": True}
    repository = get_global_repository(tmp_path)
    repository.write_rows([row])

    class Scanner:
        def scan(self, *_args, **_kwargs):
            yield {**row, "rel": "photo.jpg"}

    service = LibraryScanService(tmp_path, scanner=Scanner())
    entered, resume = Event(), Event()
    commit = ScanLibraryUseCase._merge_chunk
    batches = []

    def delayed_commit(self, *args, **kwargs):
        entered.set()
        assert resume.wait(5)
        return commit(self, *args, **kwargs)

    with patch.object(ScanLibraryUseCase, "_merge_chunk", delayed_commit):
        with ThreadPoolExecutor(max_workers=1) as executor:
            scan = executor.submit(
                service.scan_album, old, persist_chunks=True, chunk_size=1,
                scan_batch_callback=batches.append,
            )
            try:
                assert entered.wait(5)
                service.rename_album(old, tmp_path / "New")
                if recreate_old_path:
                    old.mkdir()
            finally:
                resume.set()
            with pytest.raises(AlbumOperationError, match="changed during.*scan"):
                scan.result(timeout=5)

    assert not repository.get_rows_by_rels(["Old/photo.jpg"])
    assert repository.get_rows_by_rels(["New/photo.jpg"])["New/photo.jpg"]["is_favorite"]
    assert not batches
