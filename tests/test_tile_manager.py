from __future__ import annotations

import threading
import time
from collections.abc import Callable

from maps.map_sources import MapBackendMetadata
from maps.map_widget.tile_manager import TileManager, _TileWorker


class _RecordingBackend:
    def __init__(self) -> None:
        self.metadata = MapBackendMetadata(0.0, 6.0, False, "vector")
        self.reenter: Callable[[], None] | None = None
        self._active_calls = 0
        self.max_active_calls = 0
        self.loaded: list[tuple[int, int, int]] = []
        self.load_thread_ids: list[int] = []
        self.shutdown_thread_id: int | None = None

    def probe(self) -> MapBackendMetadata:
        return self.metadata

    def load_tile(self, z: int, x: int, y: int) -> object:
        self._active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self._active_calls)
        self.loaded.append((z, x, y))
        self.load_thread_ids.append(threading.get_ident())
        if self.reenter is not None:
            callback, self.reenter = self.reenter, None
            callback()
        self._active_calls -= 1
        return {"tile": (z, x, y)}

    def clear_cache(self) -> None:
        return None

    def shutdown(self) -> None:
        self.shutdown_thread_id = threading.get_ident()

    def set_device_scale(self, scale: float) -> None:
        del scale


def _process_until(qapp, predicate, *, timeout_seconds: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    assert predicate()


def test_tile_worker_serializes_synchronous_reentrant_requests(qapp) -> None:
    backend = _RecordingBackend()
    worker = _TileWorker(backend)
    loaded: list[tuple[int, int, int]] = []
    worker.tile_loaded.connect(lambda z, x, y, _tile: loaded.append((z, x, y)))
    backend.reenter = lambda: worker.request_tile(1, 2, 4)

    worker.request_tile(1, 2, 3)
    _process_until(qapp, lambda: len(loaded) == 2)

    assert loaded == [(1, 2, 3), (1, 2, 4)]
    assert backend.max_active_calls == 1


def test_tile_manager_loads_in_order_and_shuts_backend_down_on_worker_thread(qapp) -> None:
    backend = _RecordingBackend()
    manager = TileManager(backend, cache_limit=8)
    loaded: list[tuple[int, int, int]] = []
    manager.tile_loaded.connect(loaded.append)
    try:
        manager.ensure_tile((1, 2, 3))
        manager.ensure_tile((1, 2, 4))
        _process_until(qapp, lambda: len(loaded) == 2)
    finally:
        manager.shutdown()

    assert loaded == [(1, 2, 3), (1, 2, 4)]
    assert backend.loaded == loaded
    assert len(set(backend.load_thread_ids)) == 1
    assert backend.shutdown_thread_id == backend.load_thread_ids[0]
    assert backend.shutdown_thread_id != threading.get_ident()
