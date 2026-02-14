"""Viewport computation helpers for the map renderer."""

from __future__ import annotations

import math
from dataclasses import dataclass

# ``MAX_TILE_ZOOM_LEVEL`` reflects the highest tile zoom level available from the
# bundled map source. When the view zoom exceeds this value we keep rendering the
# level-6 tiles and upscale them so that the user never sees empty space.
MAX_TILE_ZOOM_LEVEL = 6
MERCATOR_LAT_BOUND = 85.05112878


@dataclass(frozen=True)
class ViewState:
    """Describe the camera parameters used for the current paint pass."""

    zoom: float
    fetch_zoom: int
    width: int
    height: int
    view_top_left_x: float
    view_top_left_y: float
    scaled_tile_size: float
    tiles_across: int


def compute_view_state(
    center_x: float,
    center_y: float,
    zoom: float,
    width: int,
    height: int,
    tile_size: int,
) -> ViewState:
    """Translate widget geometry into the parameters used during rendering."""

    world_size = tile_size * (2 ** zoom)
    center_px = center_x * world_size
    center_py = center_y * world_size
    view_top_left_x = center_px - width / 2.0
    view_top_left_y = center_py - height / 2.0

    # Clamp the requested tile zoom level to the available range so that
    # the renderer keeps drawing level-6 tiles when the interactive zoom is
    # higher than the tile set supports. The resulting ``scale_factor``
    # ensures that those tiles are magnified to match the desired view.
    fetch_zoom = min(MAX_TILE_ZOOM_LEVEL, max(0, math.floor(zoom)))
    tiles_across = 1 << fetch_zoom if fetch_zoom >= 0 else 1
    scale_factor = 2 ** (zoom - fetch_zoom)
    scaled_tile_size = tile_size * scale_factor

    return ViewState(
        zoom=zoom,
        fetch_zoom=fetch_zoom,
        width=width,
        height=height,
        view_top_left_x=view_top_left_x,
        view_top_left_y=view_top_left_y,
        scaled_tile_size=scaled_tile_size,
        tiles_across=tiles_across,
    )
