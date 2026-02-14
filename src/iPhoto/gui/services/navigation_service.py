"""NavigationService — page navigation management.

Replaces navigation logic previously embedded in Coordinators.
Pure Python, no Qt dependency.
"""

from __future__ import annotations

from typing import Any, Dict

from iPhoto.gui.viewmodels.signal import Signal


class NavigationService:
    """Page navigation manager — replaces Coordinator navigation logic."""

    def __init__(self) -> None:
        self.page_changed = Signal()  # emits (page_name: str, params: dict)
        self._history: list[tuple[str, Dict[str, Any]]] = []

    def navigate_to(self, page: str, **params: Any) -> None:
        """Navigate to *page* with optional *params*."""
        self._history.append((page, params))
        self.page_changed.emit(page, params)

    def go_back(self) -> bool:
        """Go back one step. Returns ``True`` if navigation occurred."""
        if len(self._history) > 1:
            self._history.pop()
            page, params = self._history[-1]
            self.page_changed.emit(page, params)
            return True
        return False

    @property
    def current_page(self) -> str | None:
        """Return the name of the current page, or ``None``."""
        if self._history:
            return self._history[-1][0]
        return None

    @property
    def current_params(self) -> Dict[str, Any]:
        """Return parameters of the current page."""
        if self._history:
            return self._history[-1][1]
        return {}

    @property
    def can_go_back(self) -> bool:
        return len(self._history) > 1

    @property
    def history_depth(self) -> int:
        return len(self._history)

    def clear_history(self) -> None:
        """Reset navigation history."""
        self._history.clear()
