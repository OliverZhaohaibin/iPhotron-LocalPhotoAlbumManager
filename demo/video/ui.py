"""GUI components — VideoEditor main window, ThumbnailBar, HandleButton."""

from __future__ import annotations

import os
import shutil
import tempfile

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, QUrl, QSize
from PySide6.QtGui import QIcon, QPixmap, QImage, QPalette, QPainter, QPen
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from config import (
    BAR_HEIGHT, THUMB_WIDTH, CORNER_RADIUS, BORDER_THICKNESS,
    THUMB_LOGICAL_HEIGHT, ARROW_THICKNESS, THEME_COLOR, HOVER_COLOR,
    ICON_PLAY, STYLESHEET,
)
from worker import ThumbnailWorker


class HandleButton(QPushButton):
    """Custom handle button: draws bold white arrows."""

    def __init__(self, arrow_type="left", parent=None):
        super().__init__(parent)
        self.arrow_type = arrow_type
        self.setFixedWidth(24)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

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
    """Thumbnail strip that draws all pixmaps via QPainter — no child widgets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(BAR_HEIGHT)
        self._pixmaps = []

        self._main_layout = QHBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # 1. Left handle
        self.btn_left = HandleButton(arrow_type="left")
        self.btn_left.setObjectName("HandleLeft")
        self.btn_left.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLOR};
                border: none;
                border-radius: 0px;
            }}
            QPushButton:hover {{ background-color: {HOVER_COLOR}; }}
        """)

        # 2. Thumbnail canvas (custom painted)
        self._canvas = _ThumbnailCanvas(self)

        # 3. Right handle
        self.btn_right = HandleButton(arrow_type="right")
        self.btn_right.setObjectName("HandleRight")
        self.btn_right.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLOR};
                border: none;
                border-top-left-radius: 0px;
                border-bottom-left-radius: 0px;
                border-top-right-radius: {CORNER_RADIUS}px;
                border-bottom-right-radius: {CORNER_RADIUS}px;
            }}
            QPushButton:hover {{ background-color: {HOVER_COLOR}; }}
        """)

        self._main_layout.addWidget(self.btn_left)
        self._main_layout.addWidget(self._canvas, stretch=1)
        self._main_layout.addWidget(self.btn_right)

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


class _ThumbnailCanvas(QWidget):
    """Lightweight widget painting QPixmaps side by side — zero child widgets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmaps = []
        self.setObjectName("StripContainer")
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def set_pixmaps(self, pixmaps):
        self._pixmaps = pixmaps
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        h = self.height()
        y_offset = BORDER_THICKNESS
        draw_h = h - 2 * BORDER_THICKNESS

        bg = self.palette().color(QPalette.Window)
        painter.fillRect(self.rect(), bg)

        x = 0
        for pm in self._pixmaps:
            if x >= self.width():
                break
            dpr = pm.devicePixelRatio() or 1.0
            logical_w = round(pm.width() / dpr)
            painter.drawPixmap(x, y_offset, logical_w, draw_h, pm)
            x += logical_w
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
