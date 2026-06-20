from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iPhoto.domain.models.query import AssetQuery, WindowResult
from iPhoto.gui.viewmodels.gallery_thumbnail_hint_loader import (
    GalleryThumbnailHintLoader,
    GalleryThumbnailHintRequest,
    _HintWorker,
)


def test_hint_worker_returns_ordered_cache_candidates_without_dto_decode() -> None:
    calls = []

    class _QueryService:
        def read_thumbnail_hint_window(self, root, query, first, limit):
            calls.append((root, query, first, limit))
            return WindowResult(
                first=first,
                rows=[
                    {"rel": "before.jpg", "thumb_cache_key": "before-key"},
                    {"rel": "visible.jpg", "thumb_cache_key": "visible-key"},
                    {"rel": "after.jpg", "thumb_cache_key": "after-key"},
                ],
                total_count=-1,
                collection_revision=0,
            )

    results = []
    request = GalleryThumbnailHintRequest(
        request_id=3,
        generation=7,
        collection_revision=11,
        root=Path("/library"),
        query=AssetQuery(),
        query_service=_QueryService(),
        first=99,
        limit=3,
        ordered_rows=(101, 99),
        guard_rows=frozenset({101}),
    )

    _HintWorker(
        request,
        SimpleNamespace(completed=SimpleNamespace(emit=results.append)),
    ).run()

    assert calls == [(Path("/library"), request.query, 99, 3)]
    assert results[0].request_id == 3
    assert results[0].collection_revision == 11
    assert [candidate.path for candidate in results[0].candidates] == [
        Path("/library/after.jpg"),
        Path("/library/before.jpg"),
    ]
    assert [candidate.kind for candidate in results[0].candidates] == [
        "guard",
        "far_speculative",
    ]


def test_discard_queued_preserves_active_hint_request() -> None:
    loader = GalleryThumbnailHintLoader()
    active = object()
    queued = object()
    loader._active = True
    loader._signals = active
    loader._queued = queued

    loader.discard_queued()

    assert loader._active is True
    assert loader._signals is active
    assert loader._queued is None
