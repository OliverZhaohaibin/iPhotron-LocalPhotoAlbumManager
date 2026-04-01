from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from maps.map_sources import MapBackendMetadata
from maps.map_widget.tile_manager import TileManager


class _ReentrantBackend:
    def __init__(self) -> None:
        self.metadata = MapBackendMetadata(0.0, 6.0, False, "vector")
        self._active_calls = 0
        self.max_active_calls = 0

    def probe(self) -> MapBackendMetadata:
        return self.metadata

    def load_tile(self, z: int, x: int, y: int) -> object:
        app = QCoreApplication.instance()
        assert app is not None

        self._active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self._active_calls)
        app.processEvents()
        self._active_calls -= 1
        return {"tile": (z, x, y)}

    def clear_cache(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def set_device_scale(self, scale: float) -> None:
        del scale


def test_tile_manager_serializes_worker_requests_under_event_reentrancy() -> None:
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])

    backend = _ReentrantBackend()
    manager = TileManager(backend, cache_limit=8)
    loaded: list[tuple[int, int, int]] = []
    loop = QEventLoop()

    def _on_loaded(key: tuple[int, int, int]) -> None:
        loaded.append(key)
        if len(loaded) == 2:
            loop.quit()

    manager.tile_loaded.connect(_on_loaded)
    manager.ensure_tile((1, 2, 3))
    manager.ensure_tile((1, 2, 4))
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    manager.shutdown()

    assert loaded == [(1, 2, 3), (1, 2, 4)]
    assert backend.max_active_calls == 1
