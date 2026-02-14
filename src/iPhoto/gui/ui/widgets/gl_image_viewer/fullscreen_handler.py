"""
Surface colour management for the GL image viewer.

Handles the immersive (fullscreen) backdrop and user-defined colour
overrides while keeping the GL clear colour and widget stylesheet in
sync.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QColor

from .utils import normalise_colour


class FullscreenHandler:
    """Manages the viewer backdrop colour state.

    Parameters
    ----------
    default_color:
        The palette-derived default surface colour.
    set_stylesheet:
        Callable to apply a CSS stylesheet string to the host widget.
    request_update:
        Callable to schedule a repaint on the host widget.
    """

    def __init__(
        self,
        default_color: QColor,
        set_stylesheet: Callable[[str], None],
        request_update: Callable[[], None],
    ) -> None:
        self._default_surface_color: QColor = QColor(default_color)
        self._surface_override: QColor | None = None
        self._backdrop_color: QColor = QColor(default_color)
        self._set_stylesheet = set_stylesheet
        self._request_update = request_update

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def backdrop_color(self) -> QColor:
        """The current GL clear colour."""
        return self._backdrop_color

    def set_surface_color_override(self, colour: str | None) -> None:
        """Override the viewer backdrop with *colour* or restore the default."""
        if colour is None:
            self._surface_override = None
        else:
            self._surface_override = normalise_colour(colour)
        self._apply()

    def set_immersive_background(self, immersive: bool) -> None:
        """Toggle the pure-black immersive backdrop used in fullscreen mode."""
        self.set_surface_color_override("#000000" if immersive else None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply(self) -> None:
        """Synchronise the widget stylesheet and GL clear colour backdrop."""
        target = self._surface_override or self._default_surface_color
        self._set_stylesheet(f"background-color: {target.name()}; border: none;")
        self._backdrop_color = QColor(target)
        self._request_update()
