"""Single-worker coordinator for asynchronous Gallery window materialization."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from iPhoto.application.dtos import AssetDTO


@dataclass(frozen=True, slots=True)
class GalleryWindowRequest:
    generation: int
    collection_revision: int
    first: int
    last: int
    window_first: int
    window_limit: int


@dataclass(frozen=True, slots=True)
class GalleryWindowResult:
    request: GalleryWindowRequest
    total_count: int
    window_first: int
    window_last: int
    rows: tuple[tuple[int, AssetDTO], ...]
    collection_revision: int
    error: str | None = None


class GalleryWindowLoader:
    """Run one Gallery window request at a time and retain only the newest pending one."""

    def __init__(
        self,
        fetch: Callable[[GalleryWindowRequest], GalleryWindowResult],
        publish: Callable[[GalleryWindowResult], None],
    ) -> None:
        self._fetch = fetch
        self._publish = publish
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="iPhotoGalleryWindow",
        )
        self._lock = threading.Lock()
        self._active = False
        self._pending: GalleryWindowRequest | None = None
        self._shutdown = False

    def submit(self, request: GalleryWindowRequest) -> None:
        with self._lock:
            if self._shutdown:
                return
            if self._active:
                self._pending = request
                return
            self._active = True
        self._start(request)

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            self._pending = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _start(self, request: GalleryWindowRequest) -> None:
        future = self._executor.submit(self._fetch, request)
        future.add_done_callback(lambda completed: self._complete(request, completed))

    def _complete(
        self,
        request: GalleryWindowRequest,
        future: Future[GalleryWindowResult],
    ) -> None:
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 - worker failures become observable results
            result = GalleryWindowResult(
                request=request,
                total_count=0,
                window_first=0,
                window_last=-1,
                rows=(),
                collection_revision=0,
                error=type(exc).__name__,
            )
        self._publish(result)

        with self._lock:
            if self._shutdown:
                self._active = False
                return
            pending = self._pending
            self._pending = None
            if pending is None:
                self._active = False
                return
        self._start(pending)
