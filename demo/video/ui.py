"""GUI components — VideoEditor main window, ThumbnailBar, HandleButton."""

from __future__ import annotations

import os
import shutil
import tempfile

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, QUrl, QSize, Signal
from PySide6.QtGui import QIcon, QPixmap, QImage, QPalette, QPainter, QPainterPath, QPen, QColor
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from config import (
    BAR_HEIGHT, THUMB_WIDTH, CORNER_RADIUS, BORDER_THICKNESS,
    HANDLE_WIDTH, THUMB_LOGICAL_HEIGHT, ARROW_THICKNESS, THEME_COLOR,
    HOVER_COLOR, TRIM_HIGHLIGHT_COLOR, MIN_TRIM_GAP, OUT_POINT_OFFSET_MS,
    ICON_PLAY, STYLESHEET,
)
from worker import ThumbnailWorker


class HandleButton(QPushButton):
    """Custom handle button: draws bold white arrows and supports dragging.

    Background painting with per-corner radii is done manually via
    QPainterPath so that rounded corners survive geometry changes during
    drag operations (Qt stylesheet border-radius can be lost after
    setGeometry / repaint cycles).
    """

    dragStarted = Signal()
    dragMoved = Signal(int)       # handle left-edge x in parent coordinates
    dragFinished = Signal()

    def __init__(self, arrow_type="left", parent=None):
        super().__init__(parent)
        self.arrow_type = arrow_type
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
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

    def set_handle_style(self, bg_color, tl=0.0, bl=0.0, tr=0.0, br=0.0,
                         allow_hover=True):
        """Set background colour and per-corner radii, then repaint."""
        self._bg_color = QColor(bg_color)
        self._corner_tl = float(tl)
        self._corner_bl = float(bl)
        self._corner_tr = float(tr)
        self._corner_br = float(br)
        self._allow_hover = allow_hover
        self.update()

    # --- hover tracking -----------------------------------------------------

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    # --- drag interaction ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._grab_offset_x = int(event.position().x())
            self.setCursor(Qt.ClosedHandCursor)
            self.dragStarted.emit()

    def mouseMoveEvent(self, event):
        if self._dragging:
            pos_in_parent = self.mapToParent(event.position().toPoint())
            self.dragMoved.emit(pos_in_parent.x() - self._grab_offset_x)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.PointingHandCursor)
            self.dragFinished.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # --- background with per-corner radii ---
        bg = (QColor(HOVER_COLOR)
              if self._hovered and self._allow_hover
              else self._bg_color)
        tl = self._corner_tl
        tr = self._corner_tr
        bl = self._corner_bl
        br = self._corner_br

        path = QPainterPath()
        path.moveTo(tl, 0)
        path.lineTo(w - tr, 0)
        if tr > 0:
            path.arcTo(w - 2 * tr, 0, 2 * tr, 2 * tr, 90, -90)
        else:
            path.lineTo(w, 0)
        path.lineTo(w, h - br)
        if br > 0:
            path.arcTo(w - 2 * br, h - 2 * br, 2 * br, 2 * br, 0, -90)
        else:
            path.lineTo(w, h)
        path.lineTo(bl, h)
        if bl > 0:
            path.arcTo(0, h - 2 * bl, 2 * bl, 2 * bl, 270, -90)
        else:
            path.lineTo(0, h)
        path.lineTo(0, tl)
        if tl > 0:
            path.arcTo(0, 0, 2 * tl, 2 * tl, 180, -90)
        else:
            path.lineTo(0, 0)
        path.closeSubpath()
        painter.fillPath(path, bg)

        # --- arrow glyph ---
        pen = QPen(Qt.white)
        pen.setWidth(ARROW_THICKNESS)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        arrow_w = 8
        arrow_h = 14
        cx = w / 2
        cy = h / 2

        if self.arrow_type == "left":
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


class ThumbnailBar(QWidget):
    """Thumbnail strip with absolutely-positioned, movable trim handles.

    The canvas fills the entire widget.  Left and right handles overlay on
    top and physically slide inward/outward as the user drags them.  Dimmed
    overlays cover the trimmed-out regions (outside the handles).

    The left handle gains rounded left-side corners when the in-point moves
    away from the left edge, and reverts to straight corners when it returns
    to the edge.
    """

    inPointChanged = Signal(float)    # 0.0 – 1.0
    outPointChanged = Signal(float)   # 0.0 – 1.0
    playheadSeeked = Signal(float)    # 0.0 – 1.0
    playheadDragStarted = Signal()
    playheadDragFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(BAR_HEIGHT)
        self._pixmaps = []

        # Trim state
        self._in_ratio = 0.0
        self._out_ratio = 1.0
        self._handle_dragging = False

        # Canvas fills the entire widget via layout
        self._main_layout = QHBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        self._canvas = _ThumbnailCanvas(self)
        self._main_layout.addWidget(self._canvas, stretch=1)

        # Handles are absolutely-positioned children (overlaid on canvas)
        self.btn_left = HandleButton(arrow_type="left", parent=self)
        self.btn_left.setObjectName("HandleLeft")
        self._apply_left_style()

        self.btn_right = HandleButton(arrow_type="right", parent=self)
        self.btn_right.setObjectName("HandleRight")
        self._apply_right_style()

        self.btn_left.raise_()
        self.btn_right.raise_()

        # --- connect handle drag signals ---
        self.btn_left.dragStarted.connect(self._on_drag_start)
        self.btn_left.dragMoved.connect(self._on_left_drag_moved)
        self.btn_left.dragFinished.connect(self._on_drag_end)

        self.btn_right.dragStarted.connect(self._on_drag_start)
        self.btn_right.dragMoved.connect(self._on_right_drag_moved)
        self.btn_right.dragFinished.connect(self._on_drag_end)

        # --- forward playhead signals from canvas ---
        self._canvas.playheadSeeked.connect(self.playheadSeeked)
        self._canvas.playheadDragStarted.connect(self.playheadDragStarted)
        self._canvas.playheadDragFinished.connect(self.playheadDragFinished)

    # --- public API ---------------------------------------------------------

    @property
    def scroll_area(self):
        """Compatibility: ThumbnailWorker uses .scroll_area.width()."""
        return self._canvas

    def add_thumbnail(self, pixmap):
        """Append a pixmap and trigger repaint — no layout relayout."""
        dpr = self.devicePixelRatioF()
        target_height_phys = int(THUMB_LOGICAL_HEIGHT * dpr)
        scaled = pixmap.scaledToHeight(
            target_height_phys, Qt.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        self._pixmaps.append(scaled)
        self._canvas.set_pixmaps(self._pixmaps)

    def clear(self):
        self._pixmaps.clear()
        self._canvas.set_pixmaps(self._pixmaps)

    def set_playhead_ratio(self, ratio):
        """Update the playhead cursor position (0.0 – 1.0)."""
        self._canvas.set_playhead(max(0.0, min(1.0, ratio)))

    # --- layout / geometry --------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_handle_positions()

    def _update_handle_positions(self):
        """Reposition handles according to current in/out ratios."""
        w = self.width()
        h = self.height()
        hw = self.btn_left.width()  # 24

        # Left handle: its left edge sits at in_ratio * w
        left_x = int(self._in_ratio * w)
        self.btn_left.setGeometry(left_x, 0, hw, h)

        # Right handle: its right edge sits at out_ratio * w
        right_x = int(self._out_ratio * w) - hw
        self.btn_right.setGeometry(right_x, 0, hw, h)

        self.btn_left.raise_()
        self.btn_right.raise_()

    # --- handle drag slots --------------------------------------------------

    def _on_drag_start(self):
        self._handle_dragging = True
        self._apply_drag_colors()

    def _on_left_drag_moved(self, handle_left_x):
        """handle_left_x = intended left edge of the left handle."""
        w = self.width()
        if w <= 0:
            return
        hw = self.btn_left.width()
        handle_left_x = max(0, min(handle_left_x, w))
        ratio = handle_left_x / w
        # Ensure handles never overlap (need ≥ 2× handle-width gap)
        min_gap = max(MIN_TRIM_GAP, 2 * hw / w)
        ratio = max(0.0, min(self._out_ratio - min_gap, ratio))
        if ratio != self._in_ratio:
            self._in_ratio = ratio
            self._canvas.set_trim(self._in_ratio, self._out_ratio)
            self._update_handle_positions()
            self.inPointChanged.emit(self._in_ratio)
            # Refresh left handle corners (rounded ↔ straight)
            self._apply_left_style(highlight=True)

    def _on_right_drag_moved(self, handle_left_x):
        """handle_left_x = intended left edge of the right handle."""
        w = self.width()
        if w <= 0:
            return
        hw = self.btn_right.width()
        handle_left_x = max(0, min(handle_left_x, w))
        # Right handle's right edge determines the out-point
        ratio = (handle_left_x + hw) / w
        min_gap = max(MIN_TRIM_GAP, 2 * hw / w)
        ratio = max(self._in_ratio + min_gap, min(1.0, ratio))
        if ratio != self._out_ratio:
            self._out_ratio = ratio
            self._canvas.set_trim(self._in_ratio, self._out_ratio)
            self._update_handle_positions()
            self.outPointChanged.emit(self._out_ratio)
            # Refresh right handle style to prevent rounded corners from
            # being lost during drag (mirrors _on_left_drag_moved pattern)
            self._apply_right_style(highlight=True)

    def _on_drag_end(self):
        self._handle_dragging = False
        self._restore_default_colors()

    # --- handle style helpers -----------------------------------------------

    def _apply_left_style(self, highlight=False):
        """Apply style to left handle — corners depend on in-point."""
        bg = TRIM_HIGHLIGHT_COLOR if highlight else THEME_COLOR
        r = float(CORNER_RADIUS) if self._in_ratio > 0 else 0.0
        self.btn_left.set_handle_style(
            bg, tl=r, bl=r, allow_hover=not highlight,
        )

    def _apply_right_style(self, highlight=False):
        """Apply style to right handle — right corners always rounded."""
        bg = TRIM_HIGHLIGHT_COLOR if highlight else THEME_COLOR
        r = float(CORNER_RADIUS)
        self.btn_right.set_handle_style(
            bg, tr=r, br=r, allow_hover=not highlight,
        )

    def _apply_drag_colors(self):
        """Turn handles + canvas border to TRIM_HIGHLIGHT_COLOR."""
        self._apply_left_style(highlight=True)
        self._apply_right_style(highlight=True)
        self._canvas.set_border_color(QColor(TRIM_HIGHLIGHT_COLOR))

    def _restore_default_colors(self):
        """Revert handles + canvas border to the default theme."""
        self._apply_left_style()
        self._apply_right_style()
        self._canvas.set_border_color(QColor(THEME_COLOR))


class _ThumbnailCanvas(QWidget):
    """Lightweight widget painting QPixmaps side by side — zero child widgets.

    Also draws a draggable playhead cursor (thin white vertical line) and
    semi-transparent dim overlays for trimmed-out regions.
    """

    playheadSeeked = Signal(float)       # ratio 0.0 – 1.0
    playheadDragStarted = Signal()
    playheadDragFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmaps = []
        self._playhead_ratio = 0.0
        self._in_ratio = 0.0
        self._out_ratio = 1.0
        self._border_color = QColor(THEME_COLOR)
        self._playhead_dragging = False
        self.setObjectName("StripContainer")
        self.setCursor(Qt.PointingHandCursor)

    def set_pixmaps(self, pixmaps):
        self._pixmaps = pixmaps
        self.update()

    def set_playhead(self, ratio):
        """Set playhead position (0.0 – 1.0) and repaint."""
        self._playhead_ratio = ratio
        self.update()

    def set_trim(self, in_ratio, out_ratio):
        """Set in/out trim ratios and repaint."""
        self._in_ratio = in_ratio
        self._out_ratio = out_ratio
        self.update()

    def set_border_color(self, color):
        """Override the border (background) colour shown around thumbnails."""
        self._border_color = QColor(color)
        self.update()

    def _inner_bounds(self, w):
        """Return (left_inner, right_inner) pixel edges inside the handles."""
        left_inner = self._in_ratio * w + HANDLE_WIDTH
        right_inner = self._out_ratio * w - HANDLE_WIDTH
        return left_inner, right_inner

    # --- playhead drag interaction ---

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._playhead_dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            self.playheadDragStarted.emit()
            self._seek_to_x(event.position().x())

    def mouseMoveEvent(self, event):
        if self._playhead_dragging:
            self._seek_to_x(event.position().x())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._playhead_dragging:
            self._playhead_dragging = False
            self.setCursor(Qt.PointingHandCursor)
            self.playheadDragFinished.emit()

    def _seek_to_x(self, x):
        w = self.width()
        if w <= 0:
            return
        left_inner, right_inner = self._inner_bounds(w)
        span = right_inner - left_inner
        ratio_span = self._out_ratio - self._in_ratio
        if span > 0 and ratio_span > 0:
            t = (x - left_inner) / span
            t = max(0.0, min(1.0, t))
            ratio = self._in_ratio + t * ratio_span
        else:
            ratio = self._in_ratio
        self._playhead_ratio = ratio
        self.update()
        self.playheadSeeked.emit(ratio)

    # --- painting ---

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        y_offset = BORDER_THICKNESS
        draw_h = h - 2 * BORDER_THICKNESS

        # Clip to bar shape — right corners rounded, left corners square
        r = float(CORNER_RADIUS)
        clip = QPainterPath()
        clip.moveTo(0, 0)                                            # top-left
        clip.lineTo(w - r, 0)                                        # top edge
        clip.arcTo(w - 2 * r, 0, 2 * r, 2 * r, 90, -90)            # top-right
        clip.lineTo(w, h - r)                                        # right edge
        clip.arcTo(w - 2 * r, h - 2 * r, 2 * r, 2 * r, 0, -90)     # bottom-right
        clip.lineTo(0, h)                                            # bottom edge
        clip.closeSubpath()                                          # left edge
        painter.setClipPath(clip)

        # 1. Default background
        painter.fillRect(self.rect(), QColor(THEME_COLOR))

        # 1b. Highlight top/bottom border strips between handles only
        left_inner, right_inner = self._inner_bounds(w)
        left_inner_i = int(left_inner)
        right_inner_i = int(right_inner)
        border_w = max(0, right_inner_i - left_inner_i)
        if border_w > 0:
            painter.fillRect(
                left_inner_i, 0, border_w, BORDER_THICKNESS,
                self._border_color,
            )
            painter.fillRect(
                left_inner_i, h - BORDER_THICKNESS, border_w, BORDER_THICKNESS,
                self._border_color,
            )

        # 2. Thumbnails
        x = 0
        for pm in self._pixmaps:
            if x >= w:
                break
            dpr = pm.devicePixelRatio() or 1.0
            logical_w = round(pm.width() / dpr)
            painter.drawPixmap(x, y_offset, logical_w, draw_h, pm)
            x += logical_w

        # 3. Trim overlays (dim regions outside in/out)
        dim = QColor(0, 0, 0, 128)
        if self._in_ratio > 0.0:
            painter.fillRect(0, 0, int(self._in_ratio * w), h, dim)
        if self._out_ratio < 1.0:
            out_x = int(self._out_ratio * w)
            painter.fillRect(out_x, 0, w - out_x, h, dim)

        # 4. Playhead cursor — thin white vertical line (inner side of handles)
        if w > 0:
            left_inner, right_inner = self._inner_bounds(w)
            span = right_inner - left_inner
            ratio_span = self._out_ratio - self._in_ratio
            if span > 0 and ratio_span > 0:
                t = (self._playhead_ratio - self._in_ratio) / ratio_span
                t = max(0.0, min(1.0, t))
                playhead_x = int(left_inner + t * span)
            else:
                playhead_x = int(left_inner)
            pen = QPen(QColor(255, 255, 255), 2)
            painter.setPen(pen)
            painter.drawLine(playhead_x, 0, playhead_x, h)

        painter.end()


class VideoEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Trimmer UI - Dynamic Fill")
        self.resize(1000, 700)
        self.setStyleSheet(STYLESHEET)

        self.temp_dir = None
        self._thumb_worker = None

        # --- Main layout ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Import button
        self.btn_import = QPushButton("Import Video")
        self.btn_import.setStyleSheet(
            "background-color: #222; color: #888; padding: 5px; border:none;",
        )
        self.btn_import.clicked.connect(self.open_file)
        main_layout.addWidget(self.btn_import)

        # 2. Video area
        self.video_widget = QVideoWidget()
        pal = self.video_widget.palette()
        pal.setColor(QPalette.Window, Qt.black)
        self.video_widget.setPalette(pal)
        self.video_widget.setAutoFillBackground(True)
        main_layout.addWidget(self.video_widget, stretch=1)

        # 3. Bottom control bar
        self.bottom_frame = QFrame()
        self.bottom_frame.setObjectName("BottomControlFrame")
        self.bottom_frame.setFixedHeight(BAR_HEIGHT + 30)

        bottom_layout = QHBoxLayout(self.bottom_frame)
        bottom_layout.setContentsMargins(20, 15, 20, 15)
        bottom_layout.setSpacing(0)

        self.controls_layout = QHBoxLayout()
        self.controls_layout.setSpacing(2)
        self.controls_layout.setContentsMargins(0, 0, 0, 0)

        # 3.1 Play button
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("PlayButton")
        self.play_btn.setFixedSize(50, BAR_HEIGHT)

        if os.path.exists(ICON_PLAY):
            self.play_btn.setIcon(QIcon(ICON_PLAY))
            self.play_btn.setIconSize(QSize(20, 20))
        else:
            self.play_btn.setText("▶")

        self.play_btn.clicked.connect(self.toggle_play)

        # 3.2 Thumbnail strip
        self.thumb_strip = ThumbnailBar()

        self.controls_layout.addWidget(self.play_btn)
        self.controls_layout.addWidget(self.thumb_strip, stretch=1)

        bottom_layout.addLayout(self.controls_layout)
        main_layout.addWidget(self.bottom_frame)

        # --- Player ---
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.mediaStatusChanged.connect(self.on_media_status)

        # Trim / playhead state
        self._duration_ms = 0
        self._in_point_ms = 0
        self._out_point_ms = 0

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.thumb_strip.inPointChanged.connect(self._on_in_point_changed)
        self.thumb_strip.outPointChanged.connect(self._on_out_point_changed)

        # Playhead scrubbing
        self._scrubbing = False
        self.thumb_strip.playheadSeeked.connect(self._on_playhead_seeked)
        self.thumb_strip.playheadDragStarted.connect(
            self._on_playhead_drag_started,
        )
        self.thumb_strip.playheadDragFinished.connect(
            self._on_playhead_drag_finished,
        )

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Video", "",
            "Video Files (*.mp4 *.mov *.avi *.mkv)",
        )
        if file_path:
            self.btn_import.hide()
            self.load_video(file_path)

    def load_video(self, file_path):
        self.player.setSource(QUrl.fromLocalFile(file_path))
        self.player.play()
        self.generate_thumbnails(file_path)

    def toggle_play(self):
        is_playing = (self.player.playbackState() == QMediaPlayer.PlayingState)
        if is_playing:
            self.player.pause()
        else:
            self.player.play()
        if not os.path.exists(ICON_PLAY):
            self.play_btn.setText("▶" if is_playing else "⏸")

    def on_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.player.setPosition(0)
            self.player.pause()
            if not os.path.exists(ICON_PLAY):
                self.play_btn.setText("▶")

    # --- trim / playhead slots ------------------------------------------------

    def _on_duration_changed(self, duration):
        self._duration_ms = duration
        self._out_point_ms = duration

    def _on_position_changed(self, position):
        if self._duration_ms <= 0 or self._scrubbing:
            return
        ratio = position / self._duration_ms
        self.thumb_strip.set_playhead_ratio(ratio)
        # Enforce out-point boundary
        if self._out_point_ms and position >= self._out_point_ms:
            self.player.pause()
            self.player.setPosition(max(self._out_point_ms - OUT_POINT_OFFSET_MS, 0))
            if not os.path.exists(ICON_PLAY):
                self.play_btn.setText("▶")

    def _on_in_point_changed(self, ratio):
        self._in_point_ms = int(ratio * self._duration_ms)
        if self.player.position() < self._in_point_ms:
            self.player.setPosition(self._in_point_ms)

    def _on_out_point_changed(self, ratio):
        self._out_point_ms = int(ratio * self._duration_ms)
        if self.player.position() > self._out_point_ms:
            self.player.setPosition(self._out_point_ms)

    def _on_playhead_seeked(self, ratio):
        """Seek the player when the user drags the playhead on the canvas."""
        if self._duration_ms <= 0:
            return
        pos = int(ratio * self._duration_ms)
        pos = max(self._in_point_ms, min(self._out_point_ms, pos))
        self.player.setPosition(pos)

    def _on_playhead_drag_started(self):
        self._scrubbing = True

    def _on_playhead_drag_finished(self):
        self._scrubbing = False

    def get_video_info(self, video_path):
        """Use ffprobe to get video duration and resolution."""
        from probe import _get_video_info
        return _get_video_info(video_path)

    def generate_thumbnails(self, video_path):
        self.thumb_strip.clear()

        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        self.temp_dir = tempfile.mkdtemp()

        dpr = self.devicePixelRatioF()
        target_height = int(THUMB_LOGICAL_HEIGHT * dpr)

        visible_width = self.thumb_strip.scroll_area.width()
        if visible_width < 100:
            visible_width = 1000

        self._thumb_worker = ThumbnailWorker(
            video_path, target_height, visible_width, self.temp_dir,
            dpr=dpr,
        )
        self._thumb_worker.thumbnail_ready.connect(
            self._on_single_thumbnail,
        )
        self._thumb_worker.thumbnails_ready.connect(
            self._on_thumbnails_ready,
        )
        self._thumb_worker.error_occurred.connect(
            self._on_thumbnail_error,
        )
        self._thumb_worker.start()

    def _on_single_thumbnail(self, result):
        """Slot: add one thumbnail as soon as it arrives."""
        if result[0] == 'pyav':
            _, w, h, buf = result
            img = QImage(
                buf, w, h, w * 3, QImage.Format.Format_RGB888,
            ).copy()
            pix = QPixmap.fromImage(img)
        elif result[0] == 'pipe':
            _, w, h, buf = result
            img = QImage(
                buf, w, h, w * 4, QImage.Format.Format_ARGB32,
            ).copy()
            pix = QPixmap.fromImage(img)
        else:
            pix = QPixmap(result[1])
        if not pix.isNull():
            self.thumb_strip.add_thumbnail(pix)

    def _on_thumbnails_ready(self, results):
        """Slot: called when all thumbnails are done (batch path)."""
        if not results:
            self._on_thumbnail_error("No thumbnails generated")
            return
        for r in results:
            if r[0] == 'pipe':
                _, w, h, buf = r
                img = QImage(
                    buf, w, h, w * 4, QImage.Format.Format_ARGB32,
                ).copy()
                pix = QPixmap.fromImage(img)
            else:
                pix = QPixmap(r[1])
            if not pix.isNull():
                self.thumb_strip.add_thumbnail(pix)

    def _on_thumbnail_error(self, msg):
        """Slot: fallback grey rectangles when generation fails."""
        print(f"Thumbnail generation error: {msg}")
        dpr = self.devicePixelRatioF()
        phys_h = int(THUMB_LOGICAL_HEIGHT * dpr)
        phys_w = int(THUMB_WIDTH * dpr)
        fallback = QPixmap(phys_w, phys_h)
        fallback.setDevicePixelRatio(dpr)
        fallback.fill(Qt.darkGray)
        for _ in range(10):
            self.thumb_strip.add_thumbnail(fallback)

    def closeEvent(self, event):
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.abort()
            self._thumb_worker.wait(3000)
        if (
            self.temp_dir
            and os.path.exists(self.temp_dir)
            and (not self._thumb_worker or not self._thumb_worker.isRunning())
        ):
            shutil.rmtree(self.temp_dir)
        event.accept()
