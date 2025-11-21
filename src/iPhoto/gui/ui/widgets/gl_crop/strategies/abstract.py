"""
Abstract base class for crop interaction strategies.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from PySide6.QtCore import QPointF


class InteractionStrategy(ABC):
    """Base class for crop interaction strategies (pan, resize, etc.)."""

    @abstractmethod
    def on_drag(self, delta_view: QPointF) -> None:
        """Handle drag movement in viewport coordinates.

        Parameters
        ----------
        delta_view:
            Movement delta in viewport coordinates.
        """

    @abstractmethod
    def on_end(self) -> None:
        """Handle end of interaction (mouse release)."""
