from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for map renderer tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtGui", reason="QtGui is required for map renderer tests", exc_type=ImportError)

from PySide6.QtGui import QImage, QPainter

from maps.map_sources import MapBackendMetadata
from maps.map_widget.map_renderer import MapRenderer
from maps.map_widget.viewport import ViewState


class _FakeTileManager:
    def __init__(self, metadata: MapBackendMetadata) -> None:
        self.metadata = metadata


def test_map_renderer_uses_fetch_max_zoom_override(monkeypatch) -> None:
    captured: dict[str, int] = {}

    def _fake_compute_view_state(
        center_x: float,
        center_y: float,
        zoom: float,
        width: int,
        height: int,
        tile_size: int,
        *,
        max_tile_zoom_level: int = 6,
    ) -> ViewState:
        del center_x, center_y, zoom, width, height, tile_size
        captured["max_tile_zoom_level"] = max_tile_zoom_level
        return ViewState(
            zoom=8.5,
            fetch_zoom=6,
            width=256,
            height=256,
            view_top_left_x=0.0,
            view_top_left_y=0.0,
            scaled_tile_size=256.0,
            tiles_across=64,
        )

    monkeypatch.setattr("maps.map_widget.map_renderer.compute_view_state", _fake_compute_view_state)
    monkeypatch.setattr("maps.map_widget.map_renderer.collect_tiles", lambda view_state, tile_manager: ([], []))
    monkeypatch.setattr("maps.map_widget.map_renderer.request_tiles", lambda tiles_to_request, tile_manager: None)
    monkeypatch.setattr("maps.map_widget.map_renderer.render_cities", lambda *args, **kwargs: [])

    renderer = MapRenderer(
        style=object(),
        tile_manager=_FakeTileManager(
            MapBackendMetadata(
                2.0,
                8.5,
                False,
                "vector",
                "tms",
                fetch_max_zoom=6,
            )
        ),
        layers=[],
        tile_size=256,
    )

    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    try:
        renderer.render(
            painter,
            center_x=0.5,
            center_y=0.5,
            zoom=8.5,
            width=256,
            height=256,
        )
    finally:
        painter.end()

    assert captured["max_tile_zoom_level"] == 6


def test_map_renderer_forces_opaque_background(monkeypatch) -> None:
    monkeypatch.setattr("maps.map_widget.map_renderer.collect_tiles", lambda view_state, tile_manager: ([], []))
    monkeypatch.setattr("maps.map_widget.map_renderer.request_tiles", lambda tiles_to_request, tile_manager: None)
    monkeypatch.setattr("maps.map_widget.map_renderer.render_cities", lambda *args, **kwargs: [])

    renderer = MapRenderer(
        style=object(),
        tile_manager=_FakeTileManager(
            MapBackendMetadata(
                2.0,
                8.5,
                False,
                "vector",
                "tms",
                fetch_max_zoom=6,
            )
        ),
        layers=[],
        tile_size=256,
    )

    image = QImage(32, 32, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    try:
        renderer.render(
            painter,
            center_x=0.5,
            center_y=0.5,
            zoom=2.0,
            width=32,
            height=32,
        )
    finally:
        painter.end()

    pixel = image.pixelColor(16, 16)
    assert pixel.alpha() == 255
    assert pixel.name().lower() == "#88a8c2"
