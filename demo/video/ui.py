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
from PySide6.QtGui import QIcon, QPixmap, QImage, QPalette, QPainter, QPen, QColor
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from config import (
    BAR_HEIGHT, THUMB_WIDTH, CORNER_RADIUS, BORDER_THICKNESS,
    THUMB_LOGICAL_HEIGHT, ARROW_THICKNESS, THEME_COLOR, HOVER_COLOR,
    TRIM_HIGHLIGHT_COLOR, MIN_TRIM_GAP, OUT_POINT_OFFSET_MS,
    ICON_PLAY, STYLESHEET,
)
from worker import ThumbnailWorker


class HandleButton(QPushButton):
    """Custom handle button: draws bold white arrows and supports dragging."""

    dragStarted = Signal()
    dragMoved = Signal(int)       # x position in parent coordinates
    dragFinished = Signal()

    def __init__(self, arrow_type="left", parent=None):
        super().__init__(parent)
        self.arrow_type = arrow_type
        self._dragging = False
        self.setFixedWidth(24)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

    # --- drag interaction ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            self.dragStarted.emit()

    def mouseMoveEvent(self, event):
        if self._dragging:
            pos_in_parent = self.mapToParent(event.position().toPoint())
            self.dragMoved.emit(pos_in_parent.x())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.PointingHandCursor)
            self.dragFinished.emit()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        pen = QPen(Qt.white)
        pen.setWidth(ARROW_THICKNESS)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        w = self.width()
        h = self.height()
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


class ThumbnailBar(QWidget):
    """Thumbnail strip that draws all pixmaps via QPainter — no child widgets.

    Supports a playhead cursor, draggable left/right handles for setting
    video in/out points, and a highlight colour while handles are dragged.
    """

    inPointChanged = Signal(float)    # 0.0 – 1.0
    outPointChanged = Signal(float)   # 0.0 – 1.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(BAR_HEIGHT)
        self._pixmaps = []

        # Trim state
        self._in_ratio = 0.0
        self._out_ratio = 1.0
        self._handle_dragging = False

        self._main_layout = QHBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # --- default stylesheets (saved for restore) ---
        self._default_style_left = f"""
            QPushButton {{
                background-color: {THEME_COLOR};
                border: none;
                border-radius: 0px;
            }}
            QPushButton:hover {{ background-color: {HOVER_COLOR}; }}
        """
        self._default_style_right = f"""
            QPushButton {{
                background-color: {THEME_COLOR};
                border: none;
                border-top-left-radius: 0px;
                border-bottom-left-radius: 0px;
                border-top-right-radius: {CORNER_RADIUS}px;
                border-bottom-right-radius: {CORNER_RADIUS}px;
            }}
            QPushButton:hover {{ background-color: {HOVER_COLOR}; }}
        """

        # 1. Left handle
        self.btn_left = HandleButton(arrow_type="left")
        self.btn_left.setObjectName("HandleLeft")
        self.btn_left.setStyleSheet(self._default_style_left)

        # 2. Thumbnail canvas (custom painted)
        self._canvas = _ThumbnailCanvas(self)

        # 3. Right handle
        self.btn_right = HandleButton(arrow_type="right")
        self.btn_right.setObjectName("HandleRight")
        self.btn_right.setStyleSheet(self._default_style_right)

        self._main_layout.addWidget(self.btn_left)
        self._main_layout.addWidget(self._canvas, stretch=1)
        self._main_layout.addWidget(self.btn_right)

        # --- connect handle drag signals ---
        self.btn_left.dragStarted.connect(self._on_drag_start)
        self.btn_left.dragMoved.connect(self._on_left_drag_moved)
        self.btn_left.dragFinished.connect(self._on_drag_end)

        self.btn_right.dragStarted.connect(self._on_drag_start)
        self.btn_right.dragMoved.connect(self._on_right_drag_moved)
        self.btn_right.dragFinished.connect(self._on_drag_end)

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

    # --- handle drag slots --------------------------------------------------

    def _on_drag_start(self):
        self._handle_dragging = True
        self._apply_drag_colors()

    def _on_left_drag_moved(self, parent_x):
        canvas_left = self.btn_left.width()
        canvas_w = self._canvas.width()
        if canvas_w <= 0:
            return
        ratio = (parent_x - canvas_left) / canvas_w
        ratio = max(0.0, min(self._out_ratio - MIN_TRIM_GAP, ratio))
        if ratio != self._in_ratio:
            self._in_ratio = ratio
            self._canvas.set_trim(self._in_ratio, self._out_ratio)
            self.inPointChanged.emit(self._in_ratio)

    def _on_right_drag_moved(self, parent_x):
        canvas_left = self.btn_left.width()
        canvas_w = self._canvas.width()
        if canvas_w <= 0:
            return
        ratio = (parent_x - canvas_left) / canvas_w
        ratio = max(self._in_ratio + MIN_TRIM_GAP, min(1.0, ratio))
        if ratio != self._out_ratio:
            self._out_ratio = ratio
            self._canvas.set_trim(self._in_ratio, self._out_ratio)
            self.outPointChanged.emit(self._out_ratio)

    def _on_drag_end(self):
        self._handle_dragging = False
        self._restore_default_colors()

    # --- drag colour helpers ------------------------------------------------

    def _apply_drag_colors(self):
        """Turn handles + canvas border to TRIM_HIGHLIGHT_COLOR."""
        hl = TRIM_HIGHLIGHT_COLOR
        self.btn_left.setStyleSheet(f"""
            QPushButton {{
                background-color: {hl}; border: none; border-radius: 0px;
            }}
        """)
        self.btn_right.setStyleSheet(f"""
            QPushButton {{
                background-color: {hl}; border: none;
                border-top-right-radius: {CORNER_RADIUS}px;
                border-bottom-right-radius: {CORNER_RADIUS}px;
            }}
        """)
        self._canvas.set_border_color(QColor(hl))

    def _restore_default_colors(self):
        """Revert handles + canvas border to the default theme."""
        self.btn_left.setStyleSheet(self._default_style_left)
        self.btn_right.setStyleSheet(self._default_style_right)
        self._canvas.set_border_color(QColor(THEME_COLOR))


class _ThumbnailCanvas(QWidget):
    """Lightweight widget painting QPixmaps side by side — zero child widgets.

    Also draws a playhead cursor (thin white vertical line) and
    semi-transparent dim overlays for trimmed-out regions.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmaps = []
        self._playhead_ratio = 0.0
        self._in_ratio = 0.0
        self._out_ratio = 1.0
        self._border_color = QColor(THEME_COLOR)
        self.setObjectName("StripContainer")
        self.setAttribute(Qt.WA_OpaquePaintEvent)

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

    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        y_offset = BORDER_THICKNESS
        draw_h = h - 2 * BORDER_THICKNESS

        # 1. Border / background colour
        painter.fillRect(self.rect(), self._border_color)

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

        # 4. Playhead cursor — thin white vertical line (cf. warmth-ui.py)
        if w > 0:
            playhead_x = int(self._playhead_ratio * w)
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
        if self._duration_ms <= 0:
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
