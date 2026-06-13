"""Single-worker coordinator for asynchronous Gallery window materialization."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Literal

from iPhoto.application.dtos import GalleryAssetDTO

GalleryWindowTier = Literal["visible", "warm"]


@dataclass(frozen=True, slots=True)
class GalleryWindowRequest:
    generation: int
    collection_revision: int
    first: int
    last: int
    window_first: int
    window_limit: int
    tier: GalleryWindowTier
    requested_at_ms: float
    retry_count: int = 0
    selection_generation: int = 0


@dataclass(frozen=True, slots=True)
class GalleryWindowResult:
    request: GalleryWindowRequest
    total_count: int
    window_first: int
    window_last: int
    rows: tuple[tuple[int, GalleryAssetDTO], ...]
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
        self._lock = threading.RLock()
        self._active = False
        self._pending_visible: GalleryWindowRequest | None = None
        self._pending_warm: GalleryWindowRequest | None = None
        self._shutdown = False

    def submit(self, request: GalleryWindowRequest) -> None:
        with self._lock:
            if self._shutdown:
                return
            if self._active:
                if request.tier == "visible":
                    self._pending_visible = request
                    self._pending_warm = None
                else:
                    self._pending_warm = request
                return
            self._active = True
        self._start(request)

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            self._pending_visible = None
            self._pending_warm = None
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
        with self._lock:
            if self._shutdown:
                self._active = False
                return
            self._publish(result)
            pending = self._pending_visible or self._pending_warm
            if self._pending_visible is not None:
                self._pending_visible = None
            else:
                self._pending_warm = None
            if pending is None:
                self._active = False
                return
        self._start(pending)
