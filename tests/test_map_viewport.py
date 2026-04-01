import math

from maps.map_widget.viewport import compute_view_state


def test_compute_view_state_uses_dynamic_max_tile_zoom() -> None:
    dynamic_state = compute_view_state(
        0.5,
        0.5,
        12.25,
        1024,
        768,
        256,
        max_tile_zoom_level=12,
    )
    legacy_state = compute_view_state(
        0.5,
        0.5,
        12.25,
        1024,
        768,
        256,
    )

    assert dynamic_state.fetch_zoom == 12
    assert math.isclose(dynamic_state.scaled_tile_size, 256 * (2 ** 0.25))
    assert legacy_state.fetch_zoom == 6
