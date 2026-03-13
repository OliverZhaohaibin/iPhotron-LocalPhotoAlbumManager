"""Edge-snap window tiling for the frameless main window.

When the user drags the title bar towards a screen edge, the window
auto-tiles into a half-screen, quarter-screen, or maximised layout –
similar to the native behaviour provided by each operating system.

Because the window uses ``Qt.FramelessWindowHint``, the desktop
compositor cannot perform this automatically.  This module reimplements
the feature in pure Qt with minor platform-specific tweaks:

* **Windows** – Aero Snap-style left/right halves, top-edge maximise,
  corner quarter-tiles.
* **macOS** – Left/right halves and top-edge fill (no quarter-tiles to
  match macOS conventions; the window is never flagged as "maximised").
* **Linux** – Same zones as Windows.  Works identically on X11 and
  Wayland since it relies only on ``QWidget`` geometry APIs.
"""

from __future__ import annotations

import sys
from enum import Enum, auto
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:  # pragma: no cover
    from PySide6.QtGui import QScreen


# ---------------------------------------------------------------------------
# Snap zone taxonomy
# ---------------------------------------------------------------------------

class SnapZone(Enum):
    """Regions of a screen edge that trigger automatic tiling."""

    NONE = auto()
    LEFT = auto()
    RIGHT = auto()
    TOP = auto()          # maximise (Windows/Linux) or fill-screen (macOS)
    TOP_LEFT = auto()
    TOP_RIGHT = auto()
    BOTTOM_LEFT = auto()
    BOTTOM_RIGHT = auto()


# ---------------------------------------------------------------------------
# Platform-specific configuration
# ---------------------------------------------------------------------------

#: Distance in logical pixels from a screen edge that activates snapping.
_EDGE_THRESHOLD = 8

#: Extra corner region size – the square at each screen corner where
#: quarter-tile zones are detected.  Only used on platforms that support
#: corner snapping.
_CORNER_SIZE = 64


def _platform() -> str:
    """Return a short platform tag: ``'win'``, ``'mac'``, or ``'linux'``."""
    if sys.platform == "win32":
        return "win"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def _supports_corner_snap() -> bool:
    """Return whether the current platform provides quarter-tile zones."""
    return _platform() != "mac"


# ---------------------------------------------------------------------------
# Snap zone detection
# ---------------------------------------------------------------------------

def detect_snap_zone(cursor_global: QPoint, screen: "QScreen | None") -> SnapZone:
    """Determine which snap zone *cursor_global* falls into.

    Parameters
    ----------
    cursor_global:
        The global (screen) position of the mouse cursor.
    screen:
        The ``QScreen`` the cursor is currently on.  If *None* the
        function returns ``SnapZone.NONE``.
    """
    if screen is None:
        return SnapZone.NONE

    avail = screen.availableGeometry()
    x, y = cursor_global.x(), cursor_global.y()

    near_left = x <= avail.left() + _EDGE_THRESHOLD
    near_right = x >= avail.right() - _EDGE_THRESHOLD
    near_top = y <= avail.top() + _EDGE_THRESHOLD
    near_bottom = y >= avail.bottom() - _EDGE_THRESHOLD

    # No edge proximity – nothing to snap.
    if not (near_left or near_right or near_top or near_bottom):
        return SnapZone.NONE

    corner_snap = _supports_corner_snap()
    in_top_corner = y <= avail.top() + _CORNER_SIZE
    in_bottom_corner = y >= avail.bottom() - _CORNER_SIZE
    in_left_corner = x <= avail.left() + _CORNER_SIZE
    in_right_corner = x >= avail.right() - _CORNER_SIZE

    # Corner zones (Windows / Linux only) --------------------------------
    if corner_snap:
        if near_left and in_top_corner and (near_top or in_left_corner):
            return SnapZone.TOP_LEFT
        if near_right and in_top_corner and (near_top or in_right_corner):
            return SnapZone.TOP_RIGHT
        if near_left and in_bottom_corner and (near_bottom or in_left_corner):
            return SnapZone.BOTTOM_LEFT
        if near_right and in_bottom_corner and (near_bottom or in_right_corner):
            return SnapZone.BOTTOM_RIGHT

    # Edge zones ----------------------------------------------------------
    if near_left:
        return SnapZone.LEFT
    if near_right:
        return SnapZone.RIGHT
    if near_top:
        return SnapZone.TOP

    return SnapZone.NONE


# ---------------------------------------------------------------------------
# Geometry calculation
# ---------------------------------------------------------------------------

def snap_geometry(zone: SnapZone, screen: "QScreen | None") -> QRect:
    """Return the target window geometry for *zone* on *screen*.

    The rectangle is expressed in global (screen) coordinates so the
    caller can apply it directly via ``QWidget.setGeometry``.
    """
    if screen is None or zone is SnapZone.NONE:
        return QRect()

    avail = screen.availableGeometry()
    ax, ay, aw, ah = avail.x(), avail.y(), avail.width(), avail.height()
    half_w = aw // 2
    half_h = ah // 2

    geometry_map = {
        SnapZone.LEFT:         QRect(ax, ay, half_w, ah),
        SnapZone.RIGHT:        QRect(ax + half_w, ay, aw - half_w, ah),
        SnapZone.TOP:          QRect(ax, ay, aw, ah),
        SnapZone.TOP_LEFT:     QRect(ax, ay, half_w, half_h),
        SnapZone.TOP_RIGHT:    QRect(ax + half_w, ay, aw - half_w, half_h),
        SnapZone.BOTTOM_LEFT:  QRect(ax, ay + half_h, half_w, ah - half_h),
        SnapZone.BOTTOM_RIGHT: QRect(ax + half_w, ay + half_h, aw - half_w, ah - half_h),
    }
    return geometry_map.get(zone, QRect())


# ---------------------------------------------------------------------------
# Visual preview overlay
# ---------------------------------------------------------------------------

_PREVIEW_RADIUS = 8
_PREVIEW_COLOR = QColor(120, 170, 255, 60)
_PREVIEW_BORDER_COLOR = QColor(100, 150, 255, 120)


class SnapPreviewOverlay(QWidget):
    """Translucent overlay that shows where the window will tile.

    The overlay is created as a **top-level frameless widget** so it can
    be positioned independently of the application window.
    """

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._target_rect = QRect()

    # -- public API --------------------------------------------------------

    def show_zone(self, rect: QRect) -> None:
        """Display (or animate) the overlay to *rect* (global coords)."""
        if rect.isEmpty():
            self.dismiss()
            return

        if rect == self._target_rect and self.isVisible():
            return

        self._target_rect = QRect(rect)
        self.setGeometry(rect)
        if not self.isVisible():
            self.show()
        self.update()

    def dismiss(self) -> None:
        """Hide the overlay."""
        if self.isVisible():
            self.hide()
        self._target_rect = QRect()

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(
            2, 2,
            self.width() - 4, self.height() - 4,
            _PREVIEW_RADIUS, _PREVIEW_RADIUS,
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_PREVIEW_COLOR)
        painter.drawPath(path)

        pen = painter.pen()
        pen.setColor(_PREVIEW_BORDER_COLOR)
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        painter.end()


# ---------------------------------------------------------------------------
# EdgeSnapHelper – stateful helper integrated into FramelessWindowManager
# ---------------------------------------------------------------------------

class EdgeSnapHelper:
    """Manage the snap-to-edge lifecycle during a title-bar drag.

    Usage inside ``FramelessWindowManager``::

        # in __init__:
        self._snap_helper = EdgeSnapHelper()

        # in _handle_title_bar_drag  (MouseMove):
        self._snap_helper.update(cursor_global, screen)

        # in _handle_title_bar_drag  (MouseButtonRelease):
        rect = self._snap_helper.commit_with_screen(screen)
        if not rect.isEmpty():
            self._window.setGeometry(rect)

        # when starting a new drag on an already-snapped window:
        self._snap_helper.begin_drag(window_geometry)
    """

    def __init__(self) -> None:
        self._overlay: SnapPreviewOverlay | None = None
        self._current_zone = SnapZone.NONE
        self._pre_snap_geometry: QRect | None = None
        self._is_snapped = False

    # -- overlay (lazy) ----------------------------------------------------

    def _ensure_overlay(self) -> SnapPreviewOverlay:
        """Create the overlay widget on first use."""
        if self._overlay is None:
            self._overlay = SnapPreviewOverlay()
        return self._overlay

    # -- drag lifecycle ----------------------------------------------------

    def begin_drag(self, current_geometry: QRect) -> None:
        """Call at the start of a drag to record the pre-snap geometry."""
        if self._is_snapped and self._pre_snap_geometry is not None:
            # Already snapped – keep the stored pre-snap geometry so that
            # dragging away restores the original size.
            pass
        else:
            self._pre_snap_geometry = QRect(current_geometry)
        self._current_zone = SnapZone.NONE

    def update(self, cursor_global: QPoint, screen: "QScreen | None") -> None:
        """Call on every mouse move during a drag."""
        zone = detect_snap_zone(cursor_global, screen)
        if zone is self._current_zone:
            return

        self._current_zone = zone
        if zone is SnapZone.NONE:
            self._ensure_overlay().dismiss()
        else:
            rect = snap_geometry(zone, screen)
            self._ensure_overlay().show_zone(rect)

    def commit_with_screen(self, screen: "QScreen | None") -> QRect:
        """Finalise the drag – return snap geometry or an empty ``QRect``.

        Call this on ``MouseButtonRelease``.  If the cursor was in a snap
        zone the corresponding tiled geometry is returned so the caller
        can apply it via ``QWidget.setGeometry``.
        """
        if self._overlay is not None:
            self._overlay.dismiss()
        zone = self._current_zone
        self._current_zone = SnapZone.NONE

        if zone is SnapZone.NONE:
            if self._is_snapped:
                self._is_snapped = False
                self._pre_snap_geometry = None
            return QRect()

        rect = snap_geometry(zone, screen)
        if not rect.isEmpty():
            self._is_snapped = True
        return rect

    def cancel(self) -> None:
        """Abort snapping without applying – e.g. Escape during drag."""
        if self._overlay is not None:
            self._overlay.dismiss()
        self._current_zone = SnapZone.NONE

    def pre_snap_geometry(self) -> QRect | None:
        """The window geometry before the most recent snap, if any."""
        return self._pre_snap_geometry

    def is_snapped(self) -> bool:
        """Whether the window is currently in a snapped state."""
        return self._is_snapped

    def cleanup(self) -> None:
        """Release the overlay widget."""
        if self._overlay is not None:
            self._overlay.dismiss()
            self._overlay.deleteLater()
            self._overlay = None

    @property
    def current_zone(self) -> SnapZone:
        return self._current_zone
