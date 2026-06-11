from __future__ import annotations

import threading

from iPhoto.gui.viewmodels.gallery_window_loader import (
    GalleryWindowLoader,
    GalleryWindowRequest,
    GalleryWindowResult,
)


def _request(generation: int, tier: str) -> GalleryWindowRequest:
    return GalleryWindowRequest(
        generation=generation,
        collection_revision=1,
        first=generation,
        last=generation,
        window_first=generation,
        window_limit=1,
        tier=tier,  # type: ignore[arg-type]
        requested_at_ms=0.0,
    )


def test_latest_visible_request_runs_before_pending_warm_request() -> None:
    release = threading.Event()
    published: list[GalleryWindowResult] = []

    def fetch(request: GalleryWindowRequest) -> GalleryWindowResult:
        if request.generation == 1:
            assert release.wait(2.0)
        return GalleryWindowResult(
            request=request,
            total_count=10,
            window_first=request.window_first,
            window_last=request.window_first,
            rows=(),
            collection_revision=request.collection_revision,
        )

    done = threading.Event()

    def publish(result: GalleryWindowResult) -> None:
        published.append(result)
        if len(published) == 2:
            done.set()

    loader = GalleryWindowLoader(fetch, publish)
    loader.submit(_request(1, "visible"))
    loader.submit(_request(2, "warm"))
    loader.submit(_request(3, "visible"))
    release.set()

    assert done.wait(2.0)
    assert [result.request.generation for result in published] == [1, 3]
    loader.shutdown()


def test_shutdown_prevents_active_request_from_publishing() -> None:
    release = threading.Event()
    finished = threading.Event()
    published: list[GalleryWindowResult] = []

    def fetch(request: GalleryWindowRequest) -> GalleryWindowResult:
        assert release.wait(2.0)
        finished.set()
        return GalleryWindowResult(
            request=request,
            total_count=1,
            window_first=request.window_first,
            window_last=request.window_first,
            rows=(),
            collection_revision=request.collection_revision,
        )

    loader = GalleryWindowLoader(fetch, published.append)
    loader.submit(_request(1, "visible"))
    loader.shutdown()
    release.set()

    assert finished.wait(2.0)
    assert published == []


def test_publish_callback_can_reenter_loader() -> None:
    published: list[GalleryWindowResult] = []
    published_event = threading.Event()
    loader = None

    def fetch(request: GalleryWindowRequest) -> GalleryWindowResult:
        return GalleryWindowResult(
            request=request,
            total_count=1,
            window_first=request.window_first,
            window_last=request.window_first,
            rows=(),
            collection_revision=request.collection_revision,
        )

    def publish(result: GalleryWindowResult) -> None:
        published.append(result)
        assert loader is not None
        loader.shutdown()
        published_event.set()

    loader = GalleryWindowLoader(fetch, publish)

    loader.submit(_request(1, "visible"))

    assert published_event.wait(2.0)
    assert len(published) == 1
