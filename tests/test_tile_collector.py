from __future__ import annotations

from maps.map_sources import MapBackendMetadata
from maps.map_widget.tile_collector import collect_tiles
from maps.map_widget.viewport import compute_view_state


class _FakeTileManager:
    def __init__(self, metadata: MapBackendMetadata) -> None:
        self.metadata = metadata

    def get_tile(self, tile_key: tuple[int, int, int]):
        del tile_key
        return None

    def is_tile_missing(self, tile_key: tuple[int, int, int]) -> bool:
        del tile_key
        return False


def test_collect_tiles_uses_tms_y_flip_for_legacy_vector_tiles() -> None:
    view_state = compute_view_state(0.5, 0.5, 2.0, 256, 256, 256, max_tile_zoom_level=2)
    manager = _FakeTileManager(MapBackendMetadata(0.0, 6.0, False, "vector", "tms"))

    _, to_request = collect_tiles(view_state, manager)

    requested_keys = [tile_key for _, tile_key in to_request]
    assert requested_keys == [(2, 1, 2), (2, 2, 2), (2, 1, 1), (2, 2, 1)]


def test_collect_tiles_uses_xyz_y_for_obf_raster_tiles() -> None:
    view_state = compute_view_state(0.5, 0.5, 2.0, 256, 256, 256, max_tile_zoom_level=2)
    manager = _FakeTileManager(MapBackendMetadata(2.0, 19.0, True, "raster", "xyz"))

    _, to_request = collect_tiles(view_state, manager)

    requested_keys = [tile_key for _, tile_key in to_request]
    assert requested_keys == [(2, 1, 1), (2, 2, 1), (2, 1, 2), (2, 2, 2)]
