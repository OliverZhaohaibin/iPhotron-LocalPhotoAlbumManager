"""City label layout and hit-testing for the map renderer."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

from .viewport import MERCATOR_LAT_BOUND, ViewState


@dataclass(frozen=True)
class CityAnnotation:
    """Descriptor describing a lightweight label drawn directly on the map."""

    longitude: float
    latitude: float
    display_name: str
    full_name: str


@dataclass
class RenderedCityLabel:
    """Runtime data cached for hit-testing rendered city annotations."""

    bounds: QRectF
    full_name: str


def lonlat_to_world(
    lon: float, lat: float, world_size: float
) -> Optional[tuple[float, float]]:
    """Convert geographic coordinates to Mercator world coordinates."""

    try:
        lon_value = float(lon)
        lat_value = float(lat)
    except (TypeError, ValueError):
        return None

    lat_value = max(min(lat_value, MERCATOR_LAT_BOUND), -MERCATOR_LAT_BOUND)
    x = (lon_value + 180.0) / 360.0 * world_size
    sin_lat = math.sin(math.radians(lat_value))
    y = (
        0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)
    ) * world_size
    return x, y


def render_cities(
    painter: QPainter,
    view_state: ViewState,
    cities: list[CityAnnotation],
    tile_size: int,
    min_fetch_level: int,
) -> list[RenderedCityLabel]:
    """Render lightweight city labels and return their screen-space bounds.

    Returns the list of :class:`RenderedCityLabel` instances that were
    successfully placed so that the caller can cache them for hit-testing.
    """

    city_labels: list[RenderedCityLabel] = []
    if not cities:
        return city_labels
    if view_state.fetch_zoom < min_fetch_level:
        return city_labels

    world_size = float(tile_size * (2 ** view_state.zoom))
    if world_size <= 0.0:
        return city_labels

    center_px = view_state.view_top_left_x + view_state.width / 2.0
    half_world = world_size / 2.0

    font = QFont("Open Sans", pointSize=11)
    font.setBold(True)
    metrics = QFontMetricsF(font)

    dot_radius = 4.0
    text_gap = 6.0
    halo_pen = QPen(QColor(255, 255, 255, 220))
    halo_pen.setWidthF(3.0)
    halo_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    halo_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    halo_pen.setCosmetic(True)
    outline_pen = QPen(QColor(255, 255, 255, 220))
    outline_pen.setWidthF(2.0)
    outline_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    outline_pen.setCosmetic(True)
    label_pen = QPen(QColor("#2b2b2b"))
    label_pen.setCosmetic(True)

    painter.save()
    painter.setFont(font)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    rendered_label_boxes: list[QRectF] = []
    for city in cities:
        if not city.display_name:
            continue

        world_position = lonlat_to_world(city.longitude, city.latitude, world_size)
        if world_position is None:
            continue
        world_x, world_y = world_position

        delta_x = world_x - center_px
        if delta_x > half_world:
            world_x -= world_size
        elif delta_x < -half_world:
            world_x += world_size

        screen_x = world_x - view_state.view_top_left_x
        screen_y = world_y - view_state.view_top_left_y

        margin = 32.0
        if (
            screen_x < -margin
            or screen_y < -margin
            or screen_x > view_state.width + margin
            or screen_y > view_state.height + margin
        ):
            continue

        text_rect = metrics.boundingRect(city.display_name)
        text_width = float(text_rect.width())
        text_height = float(text_rect.height())
        baseline_y = screen_y + text_height / 2.0 - metrics.descent()
        text_top = baseline_y - metrics.ascent()
        text_left = screen_x + dot_radius + text_gap

        text_path = QPainterPath()
        text_path.addText(QPointF(text_left, baseline_y), font, city.display_name)

        painter.setPen(halo_pen)
        painter.drawPath(text_path)

        painter.setPen(outline_pen)
        painter.setBrush(QColor("#1e73ff"))
        painter.drawEllipse(QPointF(screen_x, screen_y), dot_radius, dot_radius)

        painter.setPen(label_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawText(QPointF(text_left, baseline_y), city.display_name)

        bounds_left = min(screen_x - dot_radius, text_left)
        bounds_top = min(screen_y - dot_radius, text_top)
        bounds_right = max(screen_x + dot_radius, text_left + text_width)
        bounds_bottom = max(screen_y + dot_radius, text_top + text_height)
        bounds = QRectF(
            bounds_left - 2.0,
            bounds_top - 2.0,
            (bounds_right - bounds_left) + 4.0,
            (bounds_bottom - bounds_top) + 4.0,
        )
        # Skip labels whose padded bounding box would overlap an annotation
        # that has already been drawn. The conservative buffer keeps the map
        # readable when multiple cities are clustered together.
        if any(bounds.intersects(existing_box) for existing_box in rendered_label_boxes):
            continue
        rendered_label_boxes.append(bounds)
        full_name = city.full_name or city.display_name
        city_labels.append(RenderedCityLabel(bounds=bounds, full_name=full_name))

    painter.restore()
    return city_labels
