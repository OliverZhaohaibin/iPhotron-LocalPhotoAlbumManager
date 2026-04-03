"""Timeline trim bar used by the video edit workflow."""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..icon import load_icon

BAR_HEIGHT = 50
THUMB_LOGICAL_HEIGHT = BAR_HEIGHT - 2 * 4
CORNER_RADIUS = 6
BORDER_THICKNESS = 4
HANDLE_WIDTH = 24
ARROW_THICKNESS = 3
THEME_COLOR = "#3a3a3a"
HOVER_COLOR = "#505050"
TRIM_HIGHLIGHT_COLOR = "#FFD60A"
BOTTOM_BG_COLOR = "#252525"
MIN_TRIM_GAP = 0.01


class _HandleButton(QPushButton):
    """Overlay trim handle with custom rounded-corner painting."""

    dragStarted = Signal()
    dragMoved = Signal(int)
    dragFinished = Signal()

    def __init__(self, arrow_type: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._arrow_type = arrow_type
        self._dragging = False
        self._grab_offset_x = 0
        self._bg_color = QColor(THEME_COLOR)
        self._corner_tl = 0.0
        self._corner_bl = 0.0
        self._corner_tr = 0.0
        self._corner_br = 0.0
        self._hovered = False
        self._allow_hover = True
        self.setFixedWidth(HANDLE_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_handle_style(
        self,
        bg_color: str | QColor,
        *,
        tl: float = 0.0,
        bl: float = 0.0,
        tr: float = 0.0,
        br: float = 0.0,
        allow_hover: bool = True,
    ) -> None:
        self._bg_color = QColor(bg_color)
        self._corner_tl = float(tl)
        self._corner_bl = float(bl)
        self._corner_tr = float(tr)
        self._corner_br = float(br)
        self._allow_hover = allow_hover
        self.update()

    def enterEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._grab_offset_x = int(event.position().x())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.dragStarted.emit()

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        if self._dragging:
            parent_x = self._parent_x_from_event(self.parentWidget(), event)
            self.dragMoved.emit(parent_x - self._grab_offset_x)

    @staticmethod
    def _parent_x_from_event(parent: QWidget | None, event) -> int:
        """Return the cursor x-position in *parent* coordinates.

        During handle drags Qt keeps delivering mouse-move events to the
        pressed button even after the cursor leaves its rect. Using the local
        event position makes the x-value clamp to the button edge, which causes
        the right trim handle to lag behind the cursor. Mapping the global
        cursor position back into the parent keeps both handles aligned with the
        actual pointer.
        """

        global_pos = event.globalPosition() if hasattr(event, "globalPosition") else QPointF()
        if parent is not None:
            return parent.mapFromGlobal(global_pos.toPoint()).x()
        return int(event.position().x())

    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.dragFinished.emit()

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()
        bg = QColor(HOVER_COLOR) if self._hovered and self._allow_hover else QColor(self._bg_color)

        if any(value > 0.0 for value in (self._corner_tl, self._corner_tr, self._corner_bl, self._corner_br)):
            painter.fillRect(self.rect(), QColor(BOTTOM_BG_COLOR))

        path = QPainterPath()
        path.moveTo(self._corner_tl, 0)
        path.lineTo(width - self._corner_tr, 0)
        if self._corner_tr > 0:
            path.arcTo(width - 2 * self._corner_tr, 0, 2 * self._corner_tr, 2 * self._corner_tr, 90, -90)
        else:
            path.lineTo(width, 0)
        path.lineTo(width, height - self._corner_br)
        if self._corner_br > 0:
            path.arcTo(width - 2 * self._corner_br, height - 2 * self._corner_br, 2 * self._corner_br, 2 * self._corner_br, 0, -90)
        else:
            path.lineTo(width, height)
        path.lineTo(self._corner_bl, height)
        if self._corner_bl > 0:
            path.arcTo(0, height - 2 * self._corner_bl, 2 * self._corner_bl, 2 * self._corner_bl, 270, -90)
        else:
            path.lineTo(0, height)
        path.lineTo(0, self._corner_tl)
        if self._corner_tl > 0:
            path.arcTo(0, 0, 2 * self._corner_tl, 2 * self._corner_tl, 180, -90)
        else:
            path.lineTo(0, 0)
        path.closeSubpath()
        painter.fillPath(path, bg)

        pen = QPen(Qt.GlobalColor.white)
        pen.setWidth(ARROW_THICKNESS)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        arrow_w = 8
        arrow_h = 14
        cx = width / 2
        cy = height / 2
        if self._arrow_type == "left":
            p1 = (cx + arrow_w / 2, cy - arrow_h / 2)
            p2 = (cx - arrow_w / 2, cy)
            p3 = (cx + arrow_w / 2, cy + arrow_h / 2)
        else:
            p1 = (cx - arrow_w / 2, cy - arrow_h / 2)
            p2 = (cx + arrow_w / 2, cy)
            p3 = (cx - arrow_w / 2, cy + arrow_h / 2)
        painter.drawLine(int(p1[0]), int(p1[1]), int(p2[0]), int(p2[1]))
        painter.drawLine(int(p2[0]), int(p2[1]), int(p3[0]), int(p3[1]))
        painter.end()


class _ThumbnailCanvas(QWidget):
    """Paint thumbnails, trim overlays, and a draggable playhead."""

    playheadSeeked = Signal(float)
    playheadDragStarted = Signal()
    playheadDragFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmaps: list[QPixmap] = []
        self._playhead_ratio = 0.0
        self._in_ratio = 0.0
        self._out_ratio = 1.0
        self._border_color = QColor(THEME_COLOR)
        self._playhead_dragging = False
        self._static_cache = QPixmap()
        self._static_cache_dirty = True
        self.setObjectName("StripContainer")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_pixmaps(self, pixmaps: Iterable[QPixmap]) -> None:
        self._pixmaps = list(pixmaps)
        self._mark_static_dirty()
        self.update()

    def set_playhead(self, ratio: float) -> None:
        new_ratio = max(0.0, min(1.0, ratio))
        new_ratio = max(self._in_ratio, min(self._out_ratio, new_ratio))
        if abs(new_ratio - self._playhead_ratio) < 1e-4 and not self._static_cache_dirty:
            return
        old_x = self._playhead_x()
        self._playhead_ratio = new_ratio
        new_x = self._playhead_x()
        if self._static_cache_dirty or old_x is None or new_x is None:
            self.update()
            return
        left = max(min(old_x, new_x) - 3, 0)
        width = min(abs(new_x - old_x) + 7, self.width() - left)
        self.update(left, 0, max(width, 1), self.height())

    def set_trim(self, in_ratio: float, out_ratio: float) -> None:
        self._in_ratio = max(0.0, min(1.0, in_ratio))
        self._out_ratio = max(self._in_ratio, min(1.0, out_ratio))
        self._playhead_ratio = max(
            self._in_ratio,
            min(self._out_ratio, self._playhead_ratio),
        )
        self._mark_static_dirty()
        self.update()

    def set_border_color(self, color: QColor) -> None:
        self._border_color = QColor(color)
        self._mark_static_dirty()
        self.update()

    def _inner_bounds(self, width: int) -> tuple[float, float]:
        left_inner = self._in_ratio * width + HANDLE_WIDTH
        right_inner = self._out_ratio * width - HANDLE_WIDTH
        return (left_inner, right_inner)

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        if event.button() == Qt.MouseButton.LeftButton:
            self._playhead_dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.playheadDragStarted.emit()
            self._seek_to_x(event.position().x())

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        if self._playhead_dragging:
            self._seek_to_x(event.position().x())

    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        if event.button() == Qt.MouseButton.LeftButton and self._playhead_dragging:
            self._playhead_dragging = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.playheadDragFinished.emit()

    def _seek_to_x(self, x_pos: float) -> None:
        width = self.width()
        if width <= 0:
            return
        max_x = max(width - 1, 1)
        ratio = max(0.0, min(1.0, float(x_pos) / float(max_x)))
        ratio = max(self._in_ratio, min(self._out_ratio, ratio))
        self._playhead_ratio = ratio
        self.update()
        self.playheadSeeked.emit(ratio)

    def resizeEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        super().resizeEvent(event)
        self._mark_static_dirty()

    def _mark_static_dirty(self) -> None:
        self._static_cache_dirty = True

    def _playhead_x(self) -> int | None:
        width = self.width()
        if width <= 0:
            return None
        max_x = max(width - 1, 0)
        ratio = max(0.0, min(1.0, self._playhead_ratio))
        x_pos = float(ratio * max_x)
        left_inner, right_inner = self._inner_bounds(width)
        if right_inner >= left_inner:
            x_pos = max(left_inner, min(right_inner, x_pos))
        return int(round(x_pos))

    def _rebuild_static_cache(self) -> None:
        width = self.width()
        height = self.height()
        if width <= 0 or height <= 0:
            self._static_cache = QPixmap()
            self._static_cache_dirty = False
            return

        dpr = self.devicePixelRatioF() or 1.0
        cache = QPixmap(int(width * dpr), int(height * dpr))
        cache.setDevicePixelRatio(dpr)
        cache.fill(Qt.GlobalColor.transparent)

        painter = QPainter(cache)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y_offset = BORDER_THICKNESS
        draw_h = max(0, height - 2 * BORDER_THICKNESS)

        radius = float(CORNER_RADIUS)
        clip = QPainterPath()
        clip.moveTo(0, 0)
        clip.lineTo(width - radius, 0)
        clip.arcTo(width - 2 * radius, 0, 2 * radius, 2 * radius, 90, -90)
        clip.lineTo(width, height - radius)
        clip.arcTo(width - 2 * radius, height - 2 * radius, 2 * radius, 2 * radius, 0, -90)
        clip.lineTo(0, height)
        clip.closeSubpath()
        painter.setClipPath(clip)

        painter.fillRect(0, 0, width, height, QColor(THEME_COLOR))

        left_inner, right_inner = self._inner_bounds(width)
        border_w = max(0, int(right_inner - left_inner))
        if border_w > 0:
            painter.fillRect(int(left_inner), 0, border_w, BORDER_THICKNESS, self._border_color)
            painter.fillRect(int(left_inner), height - BORDER_THICKNESS, border_w, BORDER_THICKNESS, self._border_color)

        x_pos = 0
        for pixmap in self._pixmaps:
            if x_pos >= width:
                break
            dpr = pixmap.devicePixelRatio() or 1.0
            logical_w = round(pixmap.width() / dpr)
            painter.drawPixmap(x_pos, y_offset, logical_w, draw_h, pixmap)
            x_pos += logical_w

        dim = QColor(0, 0, 0, 128)
        if self._in_ratio > 0.0:
            painter.fillRect(0, 0, int(self._in_ratio * width), height, dim)
        if self._out_ratio < 1.0:
            out_x = int(self._out_ratio * width)
            painter.fillRect(out_x, 0, width - out_x, height, dim)

        painter.end()
        self._static_cache = cache
        self._static_cache_dirty = False

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        del event
        if self._static_cache_dirty:
            self._rebuild_static_cache()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        height = self.height()
        if not self._static_cache.isNull():
            painter.drawPixmap(0, 0, self._static_cache)

        if width > 0:
            playhead_x = self._playhead_x()
            if playhead_x is None:
                painter.end()
                return
            pen = QPen(QColor(255, 255, 255), 2)
            painter.setPen(pen)
            painter.drawLine(playhead_x, 0, playhead_x, height)
        painter.end()


class VideoTrimBar(QWidget):
    """Timeline widget matching the trim interaction from the video demo."""

    playPauseRequested = Signal()
    inPointChanged = Signal(float)
    outPointChanged = Signal(float)
    playheadSeeked = Signal(float)
    playheadDragStarted = Signal()
    playheadDragFinished = Signal()
    trimDragStarted = Signal()
    trimDragFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(BAR_HEIGHT + 30)
        self._pixmaps: list[QPixmap] = []
        self._in_ratio = 0.0
        self._out_ratio = 1.0
        self._handle_dragging = False
        self._playing = False
        self._play_icon = load_icon("play.fill.svg")
        self._pause_icon = load_icon("pause.fill.svg")

        self.setStyleSheet(
            f"""
            QFrame#BottomControlFrame {{
                background-color: {BOTTOM_BG_COLOR};
                border-top: 1px solid #333333;
            }}
            QPushButton#PlayButton {{
                background-color: {THEME_COLOR};
                border: none;
                border-top-left-radius: {CORNER_RADIUS}px;
                border-bottom-left-radius: {CORNER_RADIUS}px;
                border-top-right-radius: 0px;
                border-bottom-right-radius: 0px;
                color: white;
            }}
            QPushButton#PlayButton:hover {{
                background-color: {HOVER_COLOR};
            }}
            QWidget#StripContainer {{
                background-color: transparent;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._bottom_frame = QFrame(self)
        self._bottom_frame.setObjectName("BottomControlFrame")
        self._bottom_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._bottom_frame.setFixedHeight(BAR_HEIGHT + 30)
        layout.addWidget(self._bottom_frame)

        bottom_layout = QHBoxLayout(self._bottom_frame)
        bottom_layout.setContentsMargins(20, 15, 20, 15)
        bottom_layout.setSpacing(0)

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(2)
        bottom_layout.addLayout(controls_layout)

        self._play_button = QPushButton(self._bottom_frame)
        self._play_button.setObjectName("PlayButton")
        self._play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_button.setFixedSize(50, BAR_HEIGHT)
        self._play_button.setIconSize(QSize(20, 20))
        self._play_button.clicked.connect(lambda checked=False: self.playPauseRequested.emit())
        controls_layout.addWidget(self._play_button)

        self._strip_host = QWidget(self._bottom_frame)
        self._strip_host.setFixedHeight(BAR_HEIGHT)
        self._strip_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        strip_layout = QHBoxLayout(self._strip_host)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        strip_layout.setSpacing(0)
        controls_layout.addWidget(self._strip_host, stretch=1)

        self._canvas = _ThumbnailCanvas(self._strip_host)
        strip_layout.addWidget(self._canvas, stretch=1)

        self._left_handle = _HandleButton("left", self._strip_host)
        self._right_handle = _HandleButton("right", self._strip_host)
        self._apply_left_style()
        self._apply_right_style()
        self._left_handle.raise_()
        self._right_handle.raise_()
        self.set_playing(False)

        self._left_handle.dragStarted.connect(self._on_drag_start)
        self._left_handle.dragMoved.connect(self._on_left_drag_moved)
        self._left_handle.dragFinished.connect(self._on_drag_end)
        self._right_handle.dragStarted.connect(self._on_drag_start)
        self._right_handle.dragMoved.connect(self._on_right_drag_moved)
        self._right_handle.dragFinished.connect(self._on_drag_end)

        self._canvas.playheadSeeked.connect(self.playheadSeeked)
        self._canvas.playheadDragStarted.connect(self.playheadDragStarted)
        self._canvas.playheadDragFinished.connect(self.playheadDragFinished)

    def clear(self) -> None:
        self._pixmaps.clear()
        self._canvas.set_pixmaps(())
        self.set_playing(False)

    def set_thumbnails(self, pixmaps: Iterable[QPixmap]) -> None:
        prepared: list[QPixmap] = []
        for pixmap in pixmaps:
            if pixmap.isNull():
                continue
            dpr = self.devicePixelRatioF()
            target_height_phys = int(THUMB_LOGICAL_HEIGHT * dpr)
            scaled = pixmap.scaledToHeight(target_height_phys, Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            prepared.append(scaled)
        self._pixmaps = prepared
        self._canvas.set_pixmaps(prepared)

    def add_thumbnail(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        dpr = self.devicePixelRatioF()
        target_height_phys = int(THUMB_LOGICAL_HEIGHT * dpr)
        scaled = pixmap.scaledToHeight(target_height_phys, Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        self._pixmaps.append(scaled)
        self._canvas.set_pixmaps(self._pixmaps)

    def set_playhead_ratio(self, ratio: float) -> None:
        self._canvas.set_playhead(ratio)

    def thumbnail_view_width(self) -> int:
        """Return the drawable width used to decide how many thumbnails to request."""

        return max(self._canvas.width(), self._strip_host.width(), 0)

    def set_trim_ratios(self, in_ratio: float, out_ratio: float) -> None:
        self._in_ratio = max(0.0, min(1.0, in_ratio))
        self._out_ratio = max(self._in_ratio, min(1.0, out_ratio))
        self._canvas.set_trim(self._in_ratio, self._out_ratio)
        self._update_handle_positions()

    def trim_ratios(self) -> tuple[float, float]:
        return (self._in_ratio, self._out_ratio)

    def is_playing(self) -> bool:
        """Return whether the trim bar transport is currently in the playing state."""

        return self._playing

    def set_playing(self, is_playing: bool) -> None:
        """Synchronise the left transport button with the active playback state."""

        self._playing = bool(is_playing)
        icon = self._pause_icon if self._playing else self._play_icon
        fallback_text = "⏸" if self._playing else "▶"
        if not icon.isNull():
            self._play_button.setIcon(icon)
            self._play_button.setText("")
        else:
            self._play_button.setIcon(icon)
            self._play_button.setText(fallback_text)

    def resizeEvent(self, event) -> None:  # pragma: no cover - GUI behaviour
        super().resizeEvent(event)
        self._update_handle_positions()

    def _update_handle_positions(self) -> None:
        width = self._strip_host.width()
        height = self._strip_host.height()
        if width <= 0 or height <= 0:
            return
        handle_width = self._left_handle.width()
        left_x = int(self._in_ratio * width)
        right_x = int(self._out_ratio * width) - handle_width
        self._left_handle.setGeometry(left_x, 0, handle_width, height)
        self._right_handle.setGeometry(right_x, 0, handle_width, height)
        self._left_handle.raise_()
        self._right_handle.raise_()

    def _on_drag_start(self) -> None:
        self._handle_dragging = True
        self.trimDragStarted.emit()
        self._apply_drag_colors()

    def _on_left_drag_moved(self, handle_left_x: int) -> None:
        width = self._strip_host.width()
        if width <= 0:
            return
        requested_left_x = int(handle_left_x)
        handle_width = self._left_handle.width()
        handle_left_x = max(0, min(requested_left_x, width))
        ratio = handle_left_x / width
        min_gap = max(MIN_TRIM_GAP, 2 * handle_width / width)
        ratio = max(0.0, min(self._out_ratio - min_gap, ratio))
        if ratio != self._in_ratio:
            self._in_ratio = ratio
            self._canvas.set_trim(self._in_ratio, self._out_ratio)
            self._update_handle_positions()
            self._apply_left_style(highlight=True)
            self.inPointChanged.emit(self._in_ratio)

    def _on_right_drag_moved(self, handle_left_x: int) -> None:
        width = self._strip_host.width()
        if width <= 0:
            return
        requested_left_x = int(handle_left_x)
        handle_width = self._right_handle.width()
        handle_left_x = max(0, min(requested_left_x, width))
        ratio = (handle_left_x + handle_width) / width
        min_gap = max(MIN_TRIM_GAP, 2 * handle_width / width)
        ratio = max(self._in_ratio + min_gap, min(1.0, ratio))
        if ratio != self._out_ratio:
            self._out_ratio = ratio
            self._canvas.set_trim(self._in_ratio, self._out_ratio)
            self._update_handle_positions()
            self._apply_right_style(highlight=True)
            self.outPointChanged.emit(self._out_ratio)

    def _on_drag_end(self) -> None:
        self._handle_dragging = False
        self._restore_default_colors()
        self.trimDragFinished.emit()

    def _apply_left_style(self, *, highlight: bool = False) -> None:
        bg = TRIM_HIGHLIGHT_COLOR if highlight else THEME_COLOR
        radius = float(CORNER_RADIUS) if self._in_ratio > 0 else 0.0
        self._left_handle.set_handle_style(bg, tl=radius, bl=radius, allow_hover=not highlight)

    def _apply_right_style(self, *, highlight: bool = False) -> None:
        bg = TRIM_HIGHLIGHT_COLOR if highlight else THEME_COLOR
        radius = float(CORNER_RADIUS)
        self._right_handle.set_handle_style(bg, tr=radius, br=radius, allow_hover=not highlight)

    def _apply_drag_colors(self) -> None:
        self._apply_left_style(highlight=True)
        self._apply_right_style(highlight=True)
        self._canvas.set_border_color(QColor(TRIM_HIGHLIGHT_COLOR))

    def _restore_default_colors(self) -> None:
        self._apply_left_style()
        self._apply_right_style()
        self._canvas.set_border_color(QColor(THEME_COLOR))


__all__ = ["VideoTrimBar"]
