from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for gallery page loader tests", exc_type=ImportError)

import shiboken6

from iPhoto.domain.models import Asset, MediaType
from iPhoto.domain.models.query import AssetQuery
from iPhoto.gui.viewmodels.gallery_page_loader import (
    GalleryPageLoader,
    GalleryPageRequest,
    _GalleryPageLoaderSignals,
    _GalleryPageTask,
)


class _FakeQueryService:
    def __init__(self, assets) -> None:
        self.assets = list(assets)

    def read_query_asset_rows(self, root: Path, query: AssetQuery):
        offset = query.offset
        limit = query.limit if query.limit is not None else len(self.assets)
        return [
            {
                "id": asset.id,
                "rel": asset.path.as_posix(),
                "media_type": 1 if asset.media_type == MediaType.VIDEO else 0,
                "bytes": asset.size_bytes,
                "w": asset.width,
                "h": asset.height,
            }
            for asset in self.assets[offset : offset + limit]
        ]


def test_gallery_page_task_skips_thumbnail_rows() -> None:
    service = _FakeQueryService(
        [
            Asset(
                id="thumb",
                album_id="a",
                path=Path("photo_256x256.jpg"),
                media_type=MediaType.IMAGE,
                width=256,
                height=256,
                size_bytes=1,
            ),
            Asset(
                id="real",
                album_id="a",
                path=Path("photo.jpg"),
                media_type=MediaType.IMAGE,
                width=4000,
                height=3000,
                size_bytes=1_000_000,
            ),
        ]
    )
    request = GalleryPageRequest(
        request_id=1,
        selection_revision=1,
        root=Path("."),
        query=AssetQuery(),
        first=0,
        last=1,
    )
    signals = _GalleryPageLoaderSignals()
    loaded = []
    signals.pageLoaded.connect(loaded.append)

    task = _GalleryPageTask(
        asset_query_service=service,
        library_root=Path("."),
        request=request,
        signals=signals,
    )
    task.run()

    assert loaded
    result = loaded[0]
    assert [dto.rel_path.name for dto in result.rows.values()] == ["photo.jpg"]


def test_gallery_page_loader_emits_page_failed_for_latest_request() -> None:
    loader = GalleryPageLoader()
    failed = []
    loader.pageFailed.connect(lambda request_id, selection_revision: failed.append((request_id, selection_revision)))

    loader._latest_request_id = 3
    loader._latest_selection_revision = 7
    loader._handle_page_failed(3, 7)

    assert failed == [(3, 7)]


def test_gallery_page_loader_tolerates_loader_teardown_during_inflight_task(qapp) -> None:
    class _CapturingThreadPool:
        def __init__(self) -> None:
            self.task = None

        def start(self, task) -> None:
            self.task = task

    service = _FakeQueryService(
        [
            Asset(
                id="real",
                album_id="a",
                path=Path("photo.jpg"),
                media_type=MediaType.IMAGE,
                width=4000,
                height=3000,
                size_bytes=1_000_000,
            ),
        ]
    )
    request = GalleryPageRequest(
        request_id=1,
        selection_revision=1,
        root=Path("."),
        query=AssetQuery(),
        first=0,
        last=0,
    )
    pool = _CapturingThreadPool()

    with patch("iPhoto.gui.viewmodels.gallery_page_loader.QThreadPool.globalInstance", return_value=pool):
        loader = GalleryPageLoader()
        loader.load(
            asset_query_service=service,
            library_root=Path("."),
            request=request,
        )

    assert pool.task is not None

    shiboken6.delete(loader)
    qapp.processEvents()

    pool.task.run()
