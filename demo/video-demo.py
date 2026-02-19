import sys
import os
import subprocess
import tempfile
import shutil
import json
import concurrent.futures
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QPushButton, QLabel, QFileDialog,
                               QScrollArea, QFrame, QSizePolicy)
from PySide6.QtCore import Qt, QUrl, QSize, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap, QPalette, QPainter, QPen
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

# --- 1. 图标路径配置 ---
BASE_PATH = r"D:\python_code\iPhoto\iPhotos\src\iPhoto\gui\ui\icon"
ICON_PLAY = os.path.join(BASE_PATH, "play.fill.svg")
ICON_LEFT = os.path.join(BASE_PATH, "chevron.left.svg")
ICON_RIGHT = os.path.join(BASE_PATH, "chevron.right.svg")

# --- 2. 尺寸与样式配置 ---
BAR_HEIGHT = 50
THUMB_WIDTH = 70  # 仅作为 fallback 默认宽度
CORNER_RADIUS = 6
BORDER_THICKNESS = 4
ARROW_THICKNESS = 3
THEME_COLOR = "#3a3a3a"
HOVER_COLOR = "#505050"

# --- 3. 样式表 (QSS) ---
STYLESHEET = f"""
QMainWindow {{
    background-color: #1e1e1e;
}}

/* 底部区域背景 */
QFrame#BottomControlFrame {{
    background-color: #252525;
    border-top: 1px solid #333;
}}

/* === 播放按钮 === */
QPushButton#PlayButton {{
    background-color: {THEME_COLOR};
    border: none;
    border-top-left-radius: {CORNER_RADIUS}px;
    border-bottom-left-radius: {CORNER_RADIUS}px;
    border-top-right-radius: 0px;
    border-bottom-right-radius: 0px;
    color: white;
}}
QPushButton#PlayButton:hover {{ background-color: {HOVER_COLOR}; }}

/* === 缩略图条中间容器 === */
QWidget#StripContainer {{
    background-color: {THEME_COLOR};
}}

QScrollArea {{
    background-color: transparent;
    border: none;
}}
"""


def _extract_segment(args):
    """
    Worker function for parallel thumbnail extraction.
    Each call extracts thumbnails for one time segment of the video.
    Runs in a subprocess with lowered priority to avoid starving the UI.
    """
    video_path, start_time, segment_duration, fps_rate, target_height, out_dir, seg_index = args

    # Lower this worker process priority so UI stays responsive
    try:
        os.nice(10)
    except (OSError, AttributeError):
        pass  # nice() not available on all platforms

    output_pattern = os.path.join(out_dir, f"seg{seg_index:03d}_%04d.jpg")

    cmd = [
        'ffmpeg',
        '-ss', f'{start_time:.4f}',
        '-t', f'{segment_duration:.4f}',
        '-i', video_path,
        '-vf', f'fps={fps_rate:.4f},scale=-1:{target_height}',
        '-q:v', '2',
        '-y',
        output_pattern,
    ]

    try:
        startupinfo = None
        popen_kwargs = {}
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            # BELOW_NORMAL_PRIORITY_CLASS on Windows
            popen_kwargs['creationflags'] = 0x00004000
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
            **popen_kwargs,
        )
        proc.wait()
    except Exception as e:
        print(f"FFmpeg segment {seg_index} error: {e}")
        return []

    # Collect generated files sorted by name
    files = sorted(
        f for f in os.listdir(out_dir)
        if f.startswith(f"seg{seg_index:03d}_") and f.endswith('.jpg')
    )
    return [os.path.join(out_dir, f) for f in files]


class ThumbnailWorker(QThread):
    """
    Background thread that orchestrates parallel ffmpeg processes via
    ProcessPoolExecutor to generate thumbnails without blocking the UI.
    """
    thumbnails_ready = Signal(list)   # emits a sorted list of file paths
    error_occurred = Signal(str)

    def __init__(self, video_path, target_height, fps_rate, duration, temp_dir,
                 num_workers=None, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.target_height = target_height
        self.fps_rate = fps_rate
        self.duration = duration
        self.temp_dir = temp_dir
        # Default: use half of available CPUs (at least 2) to leave headroom for UI
        if num_workers is None:
            num_workers = max(2, (os.cpu_count() or 4) // 2)
        self.num_workers = num_workers

    def run(self):
        try:
            num_segments = self.num_workers
            seg_len = self.duration / num_segments

            tasks = []
            for i in range(num_segments):
                start = i * seg_len
                tasks.append((
                    self.video_path,
                    start,
                    seg_len,
                    self.fps_rate,
                    self.target_height,
                    self.temp_dir,
                    i,
                ))

            all_paths = []
            with concurrent.futures.ProcessPoolExecutor(max_workers=self.num_workers) as pool:
                futures = {pool.submit(_extract_segment, t): t[-1] for t in tasks}
                results = {}
                for future in concurrent.futures.as_completed(futures):
                    seg_idx = futures[future]
                    results[seg_idx] = future.result()

            # Reassemble in segment order
            for idx in sorted(results.keys()):
                all_paths.extend(results[idx])

            self.thumbnails_ready.emit(all_paths)
        except Exception as e:
            self.error_occurred.emit(str(e))


class HandleButton(QPushButton):
    """
    自定义手柄按钮：绘制加粗白色箭头
    """

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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(BAR_HEIGHT)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # 1. 左手柄
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

        # 2. 滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.strip_widget = QWidget()
        self.strip_widget.setObjectName("StripContainer")

        self.strip_layout = QHBoxLayout(self.strip_widget)
        self.strip_layout.setSpacing(0)
        self.strip_layout.setContentsMargins(0, BORDER_THICKNESS, 0, BORDER_THICKNESS)
        self.strip_layout.addStretch()

        self.scroll_area.setWidget(self.strip_widget)

        # 3. 右手柄
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

        self.layout.addWidget(self.btn_left)
        self.layout.addWidget(self.scroll_area)
        self.layout.addWidget(self.btn_right)

    def add_thumbnail(self, pixmap):
        """
        核心修改逻辑：
        按高度缩放图片，保持原始宽高比，不强制拉伸宽度。
        """
        # 移除末尾的弹簧，以便添加新图
        if self.strip_layout.count() > 0:
            item = self.strip_layout.itemAt(self.strip_layout.count() - 1)
            if item.spacerItem():
                self.strip_layout.removeItem(item)

        lbl = QLabel()

        # 计算目标高度 (总高度 - 上下边距)
        target_height = BAR_HEIGHT - (2 * BORDER_THICKNESS)

        # 1. 核心：使用 SmoothTransformation 保持比例缩放到目标高度
        scaled_pixmap = pixmap.scaledToHeight(target_height, Qt.SmoothTransformation)

        lbl.setPixmap(scaled_pixmap)

        # 2. 核心：不要设置 scaledContents(True)，因为这会允许拉伸。
        lbl.setScaledContents(False)

        # 3. 设置固定大小为图片实际大小 (宽度自适应，高度固定)
        lbl.setFixedSize(scaled_pixmap.width(), target_height)

        self.strip_layout.addWidget(lbl)
        self.strip_layout.addStretch()  # 重新添加弹簧保持左对齐

    def clear(self):
        while self.strip_layout.count():
            item = self.strip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


class VideoEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Trimmer UI - Dynamic Fill")
        self.resize(1000, 700)
        self.setStyleSheet(STYLESHEET)

        self.temp_dir = None
        self._thumb_worker = None

        # --- 主布局 ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. 顶部导入
        self.btn_import = QPushButton("Import Video")
        self.btn_import.setStyleSheet("background-color: #222; color: #888; padding: 5px; border:none;")
        self.btn_import.clicked.connect(self.open_file)
        main_layout.addWidget(self.btn_import)

        # 2. 视频区域
        self.video_widget = QVideoWidget()
        pal = self.video_widget.palette()
        pal.setColor(QPalette.Window, Qt.black)
        self.video_widget.setPalette(pal)
        self.video_widget.setAutoFillBackground(True)
        main_layout.addWidget(self.video_widget, stretch=1)

        # 3. 底部控制条
        self.bottom_frame = QFrame()
        self.bottom_frame.setObjectName("BottomControlFrame")
        self.bottom_frame.setFixedHeight(BAR_HEIGHT + 30)

        bottom_layout = QHBoxLayout(self.bottom_frame)
        bottom_layout.setContentsMargins(20, 15, 20, 15)
        bottom_layout.setSpacing(0)

        # 组合控件容器
        self.controls_layout = QHBoxLayout()
        self.controls_layout.setSpacing(2)
        self.controls_layout.setContentsMargins(0, 0, 0, 0)

        # 3.1 播放按钮
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("PlayButton")
        self.play_btn.setFixedSize(50, BAR_HEIGHT)

        if os.path.exists(ICON_PLAY):
            self.play_btn.setIcon(QIcon(ICON_PLAY))
            self.play_btn.setIconSize(QSize(20, 20))
        else:
            self.play_btn.setText("▶")

        self.play_btn.clicked.connect(self.toggle_play)

        # 3.2 缩略图条
        self.thumb_strip = ThumbnailBar()

        self.controls_layout.addWidget(self.play_btn)
        self.controls_layout.addWidget(self.thumb_strip, stretch=1)

        bottom_layout.addLayout(self.controls_layout)
        main_layout.addWidget(self.bottom_frame)

        # --- 播放器 ---
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.mediaStatusChanged.connect(self.on_media_status)

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Video", "", "Video Files (*.mp4 *.mov *.avi *.mkv)")
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
        """使用 ffprobe 获取视频时长和分辨率"""
        try:
            cmd = [
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height,duration',
                '-of', 'json',
                video_path
            ]

            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            result = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo)
            data = json.loads(result.stdout)

            stream = data['streams'][0]
            width = int(stream['width'])
            height = int(stream['height'])

            # duration 可能在 stream 中，也可能在 format 中
            duration = float(stream.get('duration', 0))

            # 如果 stream 里没有 duration，尝试从 format 里取
            if duration == 0:
                cmd_format = [
                    'ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', video_path
                ]
                res_fmt = subprocess.run(cmd_format, capture_output=True, text=True, startupinfo=startupinfo)
                data_fmt = json.loads(res_fmt.stdout)
                duration = float(data_fmt['format']['duration'])

            return width, height, duration
        except Exception as e:
            print(f"Error getting video info: {e}")
            return 0, 0, 0

    def generate_thumbnails(self, video_path):
        self.thumb_strip.clear()

        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        self.temp_dir = tempfile.mkdtemp()

        # 1. 目标高度
        target_height = BAR_HEIGHT - (2 * BORDER_THICKNESS)

        # 2. 获取视频元数据
        v_w, v_h, duration = self.get_video_info(video_path)

        fps_rate = 1.0  # default

        if v_w > 0 and v_h > 0 and duration > 0:
            scaled_width = v_w * (target_height / v_h)

            visible_width = self.thumb_strip.scroll_area.width()
            if visible_width < 100:
                visible_width = 1000

            count_needed = int(visible_width / scaled_width) + 5
            fps_rate = max(0.2, count_needed / duration)
            fps_rate = min(fps_rate, 30.0)

            print(f"Vertical Video Check: {v_w}x{v_h}, Duration: {duration}s")
            print(f"Calculated: Need ~{count_needed} imgs, FPS set to: {fps_rate:.2f}")
        else:
            # ffprobe failed — use fallback
            duration = 10.0

        # 3. Launch parallel thumbnail generation in background thread
        self._thumb_worker = ThumbnailWorker(
            video_path, target_height, fps_rate, duration, self.temp_dir,
        )
        self._thumb_worker.thumbnails_ready.connect(self._on_thumbnails_ready)
        self._thumb_worker.error_occurred.connect(self._on_thumbnail_error)
        self._thumb_worker.start()

    def _on_thumbnails_ready(self, paths):
        """Slot: called on the main/UI thread when all thumbnails are done."""
        if not paths:
            self._on_thumbnail_error("No thumbnails generated")
            return
        for p in paths:
            pix = QPixmap(p)
            if not pix.isNull():
                self.thumb_strip.add_thumbnail(pix)

    def _on_thumbnail_error(self, msg):
        """Slot: fallback grey rectangles when generation fails."""
        print(f"Thumbnail generation error: {msg}")
        target_height = BAR_HEIGHT - (2 * BORDER_THICKNESS)
        fallback = QPixmap(THUMB_WIDTH, target_height)
        fallback.fill(Qt.darkGray)
        for _ in range(10):
            self.thumb_strip.add_thumbnail(fallback)

    def closeEvent(self, event):
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.wait(3000)
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoEditor()
    window.show()
    sys.exit(app.exec())