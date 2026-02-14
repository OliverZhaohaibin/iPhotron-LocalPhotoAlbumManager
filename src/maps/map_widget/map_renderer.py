"""Rendering logic for :class:`~map_widget.map_widget.MapWidget`."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Dict, Iterable, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

from maps.style_resolver import StyleResolver

from .city_label_layout import CityAnnotation, RenderedCityLabel, render_cities  # noqa: F401 – re-export
from .geometry import extract_geometry, normalize_lines, normalize_points, normalize_polygons
from .layer import LayerPlan
from .tile_collector import collect_tiles, request_tiles
from .tile_manager import TileManager
from .viewport import ViewState, compute_view_state

# Backward-compatible re-exports so that ``from .map_renderer import
# CityAnnotation`` continues to work for all existing importers.
# The ``CityAnnotation`` import above already makes the name available in
# this module's namespace; the comment is kept for clarity.


class MapRenderer:
    """Render MapLibre vector tiles using the provided tile manager."""

    CITY_LABEL_MIN_FETCH_LEVEL = 5

    def __init__(
        self,
        *,
        style: StyleResolver,
        tile_manager: TileManager,
        layers: Sequence[LayerPlan],
        tile_size: int,
    ) -> None:
        self._style = style
        self._tile_manager = tile_manager
        self._layers = list(layers)
        self._tile_size = tile_size
        self._path_cache: Dict[tuple, QPainterPath] = {}
        # ``_cities`` holds the source annotations supplied by the UI layer while
        # ``_city_labels`` caches the screen-space bounds calculated during the
        # most recent render pass.  The cached rectangles allow the widget to
        # answer hover tooltips without re-running the projection math on every
        # mouse move event.
        self._cities: list[CityAnnotation] = []
        self._city_labels: list[RenderedCityLabel] = []
        # ``_label_collision_boxes`` tracks the rectangles of text labels that
        # were successfully drawn in the current frame.  Keeping the rectangles
        # grouped by layer enables lightweight collision detection so that we can
        # suppress overlapping country names when zoomed far out while still
        # letting close-up views render every label.
        self._label_collision_boxes: dict[str, list[QRectF]] = {}

    # ------------------------------------------------------------------
    def set_cities(self, cities: Iterable[CityAnnotation]) -> None:
        """Replace the annotations drawn directly on top of the map tiles."""

        self._cities = [
            CityAnnotation(city.longitude, city.latitude, city.display_name, city.full_name)
            for city in cities
        ]
        self._city_labels.clear()

    # ------------------------------------------------------------------
    def render(
        self,
        painter: QPainter,
        *,
        center_x: float,
        center_y: float,
        zoom: float,
        width: int,
        height: int,
    ) -> None:
        """Draw the current map scene into ``painter``."""

        # ``fillRect`` clears the previous frame and establishes the water-colored
        # background used across the application.
        painter.fillRect(0, 0, width, height, QColor("#88a8c2"))

        view_state = compute_view_state(center_x, center_y, zoom, width, height, self._tile_size)
        # Collision tracking must start fresh for every paint pass because the
        # widget rerenders the entire viewport each time.
        self._label_collision_boxes.clear()
        tiles_to_draw, tiles_to_request = collect_tiles(view_state, self._tile_manager)
        request_tiles(tiles_to_request, self._tile_manager)
        self._render_tiles(painter, tiles_to_draw, view_state)
        self._city_labels = render_cities(
            painter,
            view_state,
            self._cities,
            self._tile_size,
            self.CITY_LABEL_MIN_FETCH_LEVEL,
        )

    # ------------------------------------------------------------------
    def invalidate_tile(self, tile_key: tuple[int, int, int]) -> None:
        """Remove cached geometry derived from ``tile_key``."""

        self._clear_tile_paths(tile_key)

    # ------------------------------------------------------------------
    def city_at(self, position: QPointF) -> Optional[str]:
        """Return the full city name for the label intersecting ``position``."""

        for label in reversed(self._city_labels):
            if label.bounds.contains(position):
                return label.full_name
        return None

    # ------------------------------------------------------------------
    def _render_tiles(
        self,
        painter: QPainter,
        tiles_to_draw: list[tuple[tuple[int, int, int], dict, float, float, int, int]],
        view_state: ViewState,
    ) -> None:
        """Iterate over the visible tiles and draw each requested layer."""

        for tile_key, tile_data, tile_origin_x, tile_origin_y, wrapped_x, tile_y in tiles_to_draw:
            for plan in self._layers:
                if not self._style.is_layer_visible(plan.style_layer, view_state.zoom):
                    continue
                layer = tile_data.get(plan.source_layer)
                if not layer:
                    continue
                extent = layer.get("extent", 4096)
                features = layer.get("features", [])
                if not features or not extent:
                    continue

                scale = view_state.scaled_tile_size / float(extent)

                if plan.kind in {"fill", "line"}:
                    painter.save()
                    transform = painter.transform()
                    transform.translate(tile_origin_x, tile_origin_y)
                    transform.scale(scale, scale)
                    painter.setTransform(transform)

                    if plan.kind == "fill":
                        self._draw_polygons(
                            painter,
                            tile_key,
                            features,
                            plan.style_layer,
                            extent,
                            wrapped_x,
                            tile_y,
                            plan.is_lonlat,
                            view_state.zoom,
                            view_state.fetch_zoom,
                        )
                    else:
                        self._draw_lines(
                            painter,
                            tile_key,
                            features,
                            plan.style_layer,
                            extent,
                            wrapped_x,
                            tile_y,
                            plan.is_lonlat,
                            view_state.zoom,
                            view_state.fetch_zoom,
                        )
                    painter.restore()
                elif plan.kind == "symbol":
                    self._draw_symbols(
                        painter,
                        features,
                        plan.style_layer,
                        extent,
                        tile_origin_x,
                        tile_origin_y,
                        view_state.scaled_tile_size,
                        wrapped_x,
                        tile_y,
                        plan.is_lonlat,
                        view_state.zoom,
                        view_state.fetch_zoom,
                    )

    # ------------------------------------------------------------------
    def _make_path_cache_key(
        self,
        tile_key: tuple[int, int, int],
        *,
        plan_kind: str,
        style_layer: str,
        feature_index: int,
        extra: object | None = None,
    ) -> tuple:
        """Build a stable key for cached geometry associated with ``tile_key``."""

        if extra is None:
            return (plan_kind, *tile_key, style_layer, feature_index)
        return (plan_kind, *tile_key, style_layer, feature_index, extra)

    # ------------------------------------------------------------------
    def _clear_tile_paths(self, tile_key: tuple[int, int, int]) -> None:
        """Remove any cached paths associated with ``tile_key``."""

        keys_to_remove = [key for key in self._path_cache if key[1:4] == tile_key]
        for key in keys_to_remove:
            del self._path_cache[key]

    # ------------------------------------------------------------------
    def _draw_polygons(
        self,
        painter: QPainter,
        tile_key: tuple[int, int, int],
        features: Sequence[dict],
        style_layer: str,
        extent: int,
        tile_x: int,
        tile_y: int,
        is_lonlat: bool,
        zoom: float,
        fetch_zoom: int,
    ) -> None:
        for feature_index, feature in enumerate(features):
            geom_type, coordinates = extract_geometry(
                feature,
                extent,
                tile_x,
                tile_y,
                is_lonlat,
                fetch_zoom,
            )
            polygons = normalize_polygons(geom_type, coordinates)
            if not polygons:
                continue

            properties = feature.get("properties", {})
            if not self._style.feature_matches_filter(style_layer, properties):
                continue

            brush, outline_pen = self._style.resolve_fill_style(style_layer, zoom, properties)
            if brush.style() == Qt.NoBrush and outline_pen is None:
                continue

            cache_key = self._make_path_cache_key(
                tile_key,
                plan_kind="fill",
                style_layer=style_layer,
                feature_index=feature_index,
            )
            path = self._path_cache.get(cache_key)
            if path is None:
                path = QPainterPath()
                path.setFillRule(Qt.OddEvenFill)
                for polygon in polygons:
                    self._append_polygon(path, polygon, extent)
                self._path_cache[cache_key] = path

            painter.save()
            painter.setBrush(brush)
            if outline_pen is not None:
                pen = QPen(outline_pen)
                pen.setCosmetic(True)
                painter.setPen(pen)
            else:
                painter.setPen(Qt.NoPen)
            painter.drawPath(path)
            painter.restore()

    # ------------------------------------------------------------------
    def _draw_lines(
        self,
        painter: QPainter,
        tile_key: tuple[int, int, int],
        features: Sequence[dict],
        style_layer: str,
        extent: int,
        tile_x: int,
        tile_y: int,
        is_lonlat: bool,
        zoom: float,
        fetch_zoom: int,
    ) -> None:
        for feature_index, feature in enumerate(features):
            geom_type, coordinates = extract_geometry(
                feature,
                extent,
                tile_x,
                tile_y,
                is_lonlat,
                fetch_zoom,
            )
            if geom_type is None:
                continue

            properties = feature.get("properties", {})
            if not self._style.feature_matches_filter(style_layer, properties):
                continue

            pen_style = self._style.resolve_line_style(style_layer, zoom, properties)
            if pen_style is None:
                continue

            cache_key = self._make_path_cache_key(
                tile_key,
                plan_kind="line",
                style_layer=style_layer,
                feature_index=feature_index,
                extra=geom_type,
            )
            path = self._path_cache.get(cache_key)
            if path is None:
                path = QPainterPath()
                if geom_type in {"LineString", "MultiLineString"}:
                    lines = normalize_lines(geom_type, coordinates)
                    for line in lines:
                        self._append_line(path, line, extent)
                else:
                    polygons = normalize_polygons(geom_type, coordinates)
                    for polygon in polygons:
                        self._append_polygon(path, polygon, extent)
                self._path_cache[cache_key] = path

            painter.save()
            pen = QPen(pen_style)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
            painter.restore()

    # ------------------------------------------------------------------
    def _draw_symbols(
        self,
        painter: QPainter,
        features: Sequence[dict],
        style_layer: str,
        extent: int,
        origin_x: float,
        origin_y: float,
        scaled_tile_size: float,
        tile_x: int,
        tile_y: int,
        is_lonlat: bool,
        zoom: float,
        fetch_zoom: int,
    ) -> None:
        if extent <= 0:
            return

        scale = scaled_tile_size / float(extent)
        collision_rects = self._label_collision_boxes.setdefault(style_layer, [])
        features_to_draw: list[dict] = list(features)
        if style_layer == "countries-label" and zoom < 4.0:
            # Natural Earth tiles expose label priority metadata (for example
            # ``scalerank``).  Sorting once per layer ensures that larger or more
            # important countries claim space first when the viewport is zoomed
            # out and screen real estate is limited.
            features_to_draw = self._prioritize_country_labels(features_to_draw)

        for feature in features_to_draw:
            geom_type, coordinates = extract_geometry(
                feature,
                extent,
                tile_x,
                tile_y,
                is_lonlat,
                fetch_zoom,
            )

            properties = feature.get("properties", {})
            if not self._style.feature_matches_filter(style_layer, properties):
                continue

            placement = self._style.get_layout(style_layer, "symbol-placement", zoom, properties)
            points = self._resolve_symbol_points(
                geom_type,
                coordinates,
                placement,
            )
            if not points:
                continue

            text_style = self._style.resolve_text_style(style_layer, zoom, properties)
            if text_style is None or not text_style.text:
                continue

            painter.save()
            font = QFont("Open Sans", pointSize=10)
            font.setPointSizeF(text_style.size)
            painter.setFont(font)

            metrics = QFontMetricsF(font)
            rect = metrics.boundingRect(text_style.text)
            half_width = rect.width() / 2.0
            baseline_offset = rect.height() / 2.0 - metrics.descent()

            allow_overlap_value = self._style.get_layout(
                style_layer, "text-allow-overlap", zoom, properties
            )
            ignore_placement_value = self._style.get_layout(
                style_layer, "text-ignore-placement", zoom, properties
            )
            allow_overlap = bool(allow_overlap_value) if isinstance(allow_overlap_value, bool) else False
            ignore_placement = (
                bool(ignore_placement_value) if isinstance(ignore_placement_value, bool) else False
            )

            for point in points:
                tile_x_unit = float(point[0])
                tile_y_unit = float(point[1])

                screen_center_x = origin_x + scale * tile_x_unit
                screen_center_y = origin_y + scale * (extent - tile_y_unit)

                baseline_x = screen_center_x - half_width
                baseline_y = screen_center_y + baseline_offset

                text_bounds = rect.translated(baseline_x, baseline_y)
                halo_padding = text_style.halo_width if text_style.halo_width > 0 else 0.0
                if halo_padding > 0.0:
                    text_bounds = text_bounds.adjusted(
                        -halo_padding,
                        -halo_padding,
                        halo_padding,
                        halo_padding,
                    )

                enforce_collisions = (
                    style_layer == "countries-label" and zoom < 4.0 and not allow_overlap and not ignore_placement
                )
                if enforce_collisions and self._rectangle_intersects_any(collision_rects, text_bounds):
                    # Skip labels that would overlap previously accepted ones so
                    # distant views remain legible.  When zooming in, every
                    # label is allowed to draw because there is enough screen
                    # space for the full set of country names.
                    continue

                if text_style.halo_color and text_style.halo_width > 0:
                    halo_path = QPainterPath()
                    halo_path.addText(QPointF(baseline_x, baseline_y), font, text_style.text)
                    halo_pen = QPen(text_style.halo_color, text_style.halo_width)
                    halo_pen.setJoinStyle(Qt.RoundJoin)
                    halo_pen.setCosmetic(True)
                    painter.setPen(halo_pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.drawPath(halo_path)

                label_pen = QPen(text_style.color)
                label_pen.setCosmetic(True)
                painter.setPen(label_pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawText(QPointF(baseline_x, baseline_y), text_style.text)

                collision_rects.append(text_bounds)

            painter.restore()

    # ------------------------------------------------------------------
    def _resolve_symbol_points(
        self,
        geom_type: str | None,
        coordinates: object,
        placement: object,
    ) -> list[Tuple[float, float]]:
        """Return representative anchor points for symbol placement.

        MapLibre styles can request that labels follow lines (``symbol-placement``
        set to ``"line"``).  The preview renderer does not support full text
        shaping along a path, so instead we place a single label near the center
        of each feature.  This keeps the output legible without rendering the
        same text dozens of times along a meridian or parallel.
        """

        placement_value = str(placement).lower() if isinstance(placement, str) else ""
        if placement_value == "line":
            anchors: list[Tuple[float, float]] = []

            # Line geometries receive a midpoint anchor that approximates the
            # behaviour of a proper path label while avoiding heavy text
            # duplication.
            if geom_type in {"LineString", "MultiLineString"}:
                for line in normalize_lines(geom_type, coordinates):
                    anchor = self._line_midpoint(line)
                    if anchor is not None:
                        anchors.append(anchor)

            # Polygon geometries occasionally share the same "line"
            # placement hint (for example graticule backgrounds).  Using the
            # outer ring ensures the label lands near the visual outline.
            elif geom_type in {"Polygon", "MultiPolygon"}:
                for polygon in normalize_polygons(geom_type, coordinates):
                    if not polygon:
                        continue
                    anchor = self._line_midpoint(polygon[0])
                    if anchor is not None:
                        anchors.append(anchor)

            if anchors:
                return anchors

        points = normalize_points(geom_type, coordinates)
        if not points:
            return []

        if placement_value == "line" and len(points) > 1:
            # As a final safeguard collapse dense point collections into a
            # single representative anchor so that fallback logic never spams
            # identical labels across the same line feature.
            middle_index = len(points) // 2
            return [points[middle_index]]

        return points

    # ------------------------------------------------------------------
    def _prioritize_country_labels(self, features: Sequence[dict]) -> list[dict]:
        """Sort country label features so higher-priority names render first."""

        def priority(feature: dict) -> tuple[float, float]:
            properties = feature.get("properties", {}) if isinstance(feature, dict) else {}
            # Smaller ``scalerank``/``labelrank`` values correspond to more
            # prominent countries in the Natural Earth dataset.  Falling back to
            # ``inf`` preserves the original ordering when metadata is missing.
            for key in ("scalerank", "labelrank", "LABELRANK"):
                value = properties.get(key)
                if isinstance(value, (int, float)):
                    return float(value), 0.0
            return float("inf"), 0.0

        enumerated = list(enumerate(features))
        enumerated.sort(key=lambda item: (priority(item[1]), item[0]))
        return [item[1] for item in enumerated]

    # ------------------------------------------------------------------
    @staticmethod
    def _rectangle_intersects_any(existing: Sequence[QRectF], candidate: QRectF) -> bool:
        """Return ``True`` when *candidate* intersects any rectangle in *existing*."""

        for rect in existing:
            if rect.intersects(candidate):
                return True
        return False

    # ------------------------------------------------------------------
    @staticmethod
    def _line_midpoint(line: Sequence[Tuple[float, float]]) -> Tuple[float, float] | None:
        """Return the coordinate located halfway along ``line``.

        The helper walks the polyline until half the total length is reached,
        interpolating the exact anchor location on the current segment.  A
        ``None`` result indicates that the polyline did not provide enough
        distinct coordinates to compute a stable midpoint.
        """

        if not line:
            return None
        if len(line) == 1:
            return float(line[0][0]), float(line[0][1])

        total_length = 0.0
        segment_lengths: list[float] = []
        for index in range(1, len(line)):
            start = line[index - 1]
            end = line[index]
            length = math.hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1]))
            segment_lengths.append(length)
            total_length += length

        if total_length <= 0.0:
            return None

        halfway = total_length / 2.0
        distance_accumulated = 0.0
        for index, length in enumerate(segment_lengths, start=1):
            if distance_accumulated + length >= halfway:
                start = line[index - 1]
                end = line[index]
                if length <= 0.0:
                    return float(start[0]), float(start[1])
                ratio = (halfway - distance_accumulated) / length
                anchor_x = float(start[0]) + (float(end[0]) - float(start[0])) * ratio
                anchor_y = float(start[1]) + (float(end[1]) - float(start[1])) * ratio
                return anchor_x, anchor_y
            distance_accumulated += length

        last_point = line[-1]
        return float(last_point[0]), float(last_point[1])

    # ------------------------------------------------------------------
    def _append_polygon(
        self,
        path: QPainterPath,
        polygon: Sequence[Sequence[Tuple[float, float]]],
        extent: int,
    ) -> None:
        """Add ``polygon`` rings to ``path`` within the tile coordinate space."""

        for ring in polygon:
            if len(ring) < 3:
                continue
            first_point = ring[0]
            path.moveTo(first_point[0], extent - first_point[1])
            for point in ring[1:]:
                path.lineTo(point[0], extent - point[1])
            path.closeSubpath()

    # ------------------------------------------------------------------
    def _append_line(
        self,
        path: QPainterPath,
        line: Sequence[Tuple[float, float]],
        extent: int,
    ) -> None:
        """Append a single line string to ``path`` in tile coordinates."""

        if len(line) < 2:
            return

        start = line[0]
        path.moveTo(start[0], extent - start[1])
        for point in line[1:]:
            path.lineTo(point[0], extent - point[1])


__all__ = ["MapRenderer", "CityAnnotation"]
