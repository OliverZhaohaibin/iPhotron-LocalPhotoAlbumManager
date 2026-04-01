from __future__ import annotations

import math

from maps.map_widget.qt_location_map_widget import (
    QtLocationMapWidget,
    lonlat_to_normalized,
    normalized_to_lonlat,
)


def test_lonlat_normalized_round_trip() -> None:
    normalized = lonlat_to_normalized(12.3456, 48.8566)
    assert normalized is not None

    lon, lat = normalized_to_lonlat(*normalized)

    assert math.isclose(lon, 12.3456, abs_tol=1e-6)
    assert math.isclose(lat, 48.8566, abs_tol=1e-6)


def test_qt_location_backend_metadata_exposes_place_labels() -> None:
    metadata = QtLocationMapWidget.BACKEND_METADATA

    assert metadata.tile_kind == "raster"
    assert metadata.provides_place_labels is True
    assert metadata.min_zoom == 2.0
    assert metadata.max_zoom == 19.0

