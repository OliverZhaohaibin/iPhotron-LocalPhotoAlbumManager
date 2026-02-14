"""Virtualized asset grid — renders only visible items to reduce memory."""

from __future__ import annotations


class VirtualAssetGrid:
    """Headless virtual-grid model.

    Calculates which items are visible given viewport dimensions and a
    scroll offset. The actual rendering is delegated to a paint callback,
    making this class testable without a real Qt environment.
    """

    def __init__(
        self,
        item_width: int = 200,
        item_height: int = 200,
        spacing: int = 4,
    ):
        self._item_width = item_width
        self._item_height = item_height
        self._spacing = spacing
        self._total_count = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_total_count(self, count: int) -> None:
        self._total_count = max(0, count)

    @property
    def total_count(self) -> int:
        return self._total_count

    def calculate_visible_range(
        self,
        viewport_width: int,
        viewport_height: int,
        scroll_y: int = 0,
    ) -> tuple[int, int]:
        """Return *(first_index, last_index_exclusive)* of visible items."""
        cols = self._columns(viewport_width)
        if cols == 0 or self._total_count == 0:
            return (0, 0)

        cell_h = self._item_height + self._spacing
        first_row = max(0, scroll_y // cell_h)
        last_row = (scroll_y + viewport_height) // cell_h + 1

        first = first_row * cols
        last = min((last_row + 1) * cols, self._total_count)
        return (first, last)

    def content_height(self, viewport_width: int) -> int:
        """Total scrollable content height in pixels."""
        cols = self._columns(viewport_width)
        if cols == 0:
            return 0
        rows = (self._total_count + cols - 1) // cols
        return rows * (self._item_height + self._spacing)

    def item_rect(self, index: int, viewport_width: int) -> tuple[int, int, int, int]:
        """Return *(x, y, w, h)* for the item at *index*."""
        cols = self._columns(viewport_width)
        if cols == 0:
            return (0, 0, self._item_width, self._item_height)
        row, col = divmod(index, cols)
        x = col * (self._item_width + self._spacing)
        y = row * (self._item_height + self._spacing)
        return (x, y, self._item_width, self._item_height)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _columns(self, viewport_width: int) -> int:
        cell_w = self._item_width + self._spacing
        return max(1, viewport_width // cell_w) if cell_w > 0 else 1
