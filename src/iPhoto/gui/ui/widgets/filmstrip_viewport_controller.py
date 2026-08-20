"""Geometry-aware horizontal viewport demand for the Filmstrip surface."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPoint

from ..models.proxy_mapping import map_to_root_source
from ..models.roles import Roles
from .gallery_scroll_controller import AssetScrollController


class FilmstripViewportController(AssetScrollController):
    """Publish source-row demand from non-uniform horizontal item geometry."""

    def __init__(self, view, publish) -> None:
        super().__init__(view, publish, surface_id="filmstrip", axis="horizontal")

    def handle_wheel(self, event) -> bool:
        # FilmstripView owns wheel-as-navigation behavior.
        del event
        return False

    def _display_edge(self) -> int:
        icon = self._view.iconSize()
        return max(1, int(icon.width()), int(icon.height()))

    def _resolve_visible_range(self, row_count: int) -> tuple[int, int] | None:
        model = self._view.model()
        viewport = self._view.viewport()
        if model is None or row_count <= 0 or viewport.width() <= 0:
            return None

        anchor = self._find_anchor_index()
        if not anchor.isValid():
            return None

        viewport_rect = viewport.rect()
        visible_rows: list[int] = []

        def collect(proxy_row: int) -> bool:
            index = model.index(proxy_row, 0)
            if not index.isValid():
                return False
            rect = self._view.visualRect(index)
            if not rect.isValid() or not rect.intersects(viewport_rect):
                return False
            if not bool(index.data(Roles.IS_SPACER)):
                source_index = map_to_root_source(index)
                if source_index.isValid():
                    visible_rows.append(int(source_index.row()))
            return True

        anchor_row = int(anchor.row())
        collect(anchor_row)

        for proxy_row in range(anchor_row - 1, -1, -1):
            index = model.index(proxy_row, 0)
            rect = self._view.visualRect(index)
            if rect.isValid() and rect.right() < viewport_rect.left():
                break
            collect(proxy_row)

        for proxy_row in range(anchor_row + 1, row_count):
            index = model.index(proxy_row, 0)
            rect = self._view.visualRect(index)
            if rect.isValid() and rect.left() > viewport_rect.right():
                break
            collect(proxy_row)

        if not visible_rows:
            return None
        return min(visible_rows), max(visible_rows)

    def _find_anchor_index(self) -> QModelIndex:
        viewport = self._view.viewport()
        width = max(1, viewport.width())
        height = max(1, viewport.height())
        center_x = width // 2
        xs = [center_x, 0, width - 1]
        for offset in range(1, 9):
            xs.extend((max(0, center_x - offset), min(width - 1, center_x + offset)))
        ys = (height // 2, max(0, min(height - 1, self._view.iconSize().height() // 2)), 1)
        seen: set[tuple[int, int]] = set()
        for y in ys:
            for x in xs:
                point = (x, y)
                if point in seen:
                    continue
                seen.add(point)
                index = self._view.indexAt(QPoint(x, y))
                if index.isValid():
                    return index
        return QModelIndex()


__all__ = ["FilmstripViewportController"]
