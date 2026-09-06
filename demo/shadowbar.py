from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


# ============================================================
# 只改这一行：替换成你要测试的真实照片地址
# ============================================================
PHOTO_PATH = r""   # 例如：r"D:\Photos\IMG_4902.jpg"


# ============================================================
# 来自 iPhotron 当前 DetailPage / Filmstrip 的关键几何参数
# ============================================================
HEADER_BUTTON_SIZE = QSize(36, 38)
HEADER_ICON_GLYPH_SIZE = QSize(24, 24)
HEADER_MARGIN_X = 12
HEADER_SPACING = 8
ZOOM_SLIDER_WIDTH = 90

FILMSTRIP_BASE_HEIGHT = 120
FILMSTRIP_HEIGHT = FILMSTRIP_BASE_HEIGHT + 12   # iPhotron 当前是 132 px
PLAYER_FILMSTRIP_GAP = 6

# 新阴影：完全 overlay，不占 layout 高度
SHADOW_HEIGHT = 28


class ShadowOverlay(QWidget):
    """
    顶部 toolbar 下方的 macOS Photos 风格阴影。

    关键点：
    - 它和 header / OpenGL viewport 是 sibling。
    - geometry 放在 viewport 的最顶端。
    - 它不占任何 layout 高度。
    - 鼠标事件直接穿透给 OpenGL viewport。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)

        # 顶部 1 px 分隔线：比现在 detailHeaderSeparator 更克制
        p.fillRect(0, 0, self.width(), 1, QColor(0, 0, 0, 30))

        # 阴影仅向下衰减，不向 toolbar 上方扩散
        g = QLinearGradient(0, 1, 0, self.height())
        g.setColorAt(0.00, QColor(0, 0, 0, 46))
        g.setColorAt(0.08, QColor(0, 0, 0, 35))
        g.setColorAt(0.22, QColor(0, 0, 0, 22))
        g.setColorAt(0.42, QColor(0, 0, 0, 11))
        g.setColorAt(0.68, QColor(0, 0, 0, 4))
        g.setColorAt(1.00, QColor(0, 0, 0, 0))
        p.fillRect(self.rect().adjusted(0, 1, 0, 0), g)


class PhotoGLViewport(QOpenGLWidget):
    """
    单文件 demo 用的 OpenGL viewport。

    iPhotron 当前 GLImageViewer 已经迁移到 QRhiWidget，但这个 demo 使用
    QOpenGLWidget 只是为了把“shadow sibling 覆盖 GPU viewport”这件事
    独立跑出来。sibling overlay 的几何关系与接入真实 GLImageViewer 相同。
    """

    def __init__(self, photo_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)

        self._image = QImage(photo_path) if photo_path else QImage()
        self._zoom = 1.0

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

    def set_zoom_percent(self, value: int) -> None:
        self._zoom = max(0.1, min(4.0, value / 100.0))
        self.update()

    def initializeGL(self) -> None:  # noqa: N802
        pass

    def resizeGL(self, w: int, h: int) -> None:  # noqa: N802
        pass

    def paintGL(self) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # 与 iPhotron viewer_surface_color 的思想一致：
        # viewer 背景取 palette(window)，而不是写死纯黑。
        surface = self.palette().color(self.backgroundRole())
        if not surface.isValid():
            surface = self.palette().window().color()
        p.fillRect(self.rect(), surface)

        if self._image.isNull():
            self._paint_fallback(p)
            return

        iw = self._image.width()
        ih = self._image.height()
        if iw <= 0 or ih <= 0:
            return

        vw = max(1, self.width())
        vh = max(1, self.height())

        fit = min(vw / iw, vh / ih)
        scale = fit * self._zoom

        dw = iw * scale
        dh = ih * scale
        x = (vw - dw) / 2.0
        y = (vh - dh) / 2.0

        p.drawImage(
            QRectF(x, y, dw, dh),
            self._image,
            QRectF(0, 0, iw, ih),
        )

    def _paint_fallback(self, p: QPainter) -> None:
        """PHOTO_PATH 为空时仍能直接启动，便于先看 shadow 几何。"""
        w = self.width()
        h = self.height()

        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0.0, QColor("#d5d8dc"))
        g.setColorAt(0.48, QColor("#8b939b"))
        g.setColorAt(1.0, QColor("#353a40"))
        p.fillRect(self.rect(), g)

        p.setPen(QColor(255, 255, 255, 215))
        f = p.font()
        f.setPointSize(16)
        f.setWeight(QFont.Weight.Medium)
        p.setFont(f)
        p.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "把文件顶部 PHOTO_PATH 换成真实照片地址",
        )


class FlatIconButton(QToolButton):
    """单文件替代项目 SVG 资源，保持 iPhotron 36x38 的真实 hit target。"""

    def __init__(
        self,
        glyph: str,
        *,
        blue: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setText(glyph)
        self.setFixedSize(HEADER_BUTTON_SIZE)
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        f = self.font()
        f.setPointSize(16)
        self.setFont(f)

        fg = "#1677ff" if blue else "#202124"
        self.setStyleSheet(
            f"""
            QToolButton {{
                border: none;
                background: transparent;
                color: {fg};
                border-radius: 8px;
                padding: 0px;
            }}
            QToolButton:hover {{
                background: rgba(0, 0, 0, 18);
            }}
            QToolButton:pressed {{
                background: rgba(0, 0, 0, 28);
            }}
            """
        )


class PlaybackHeader(QWidget):
    """
    按当前 iPhotron DetailPageWidget._build_header() 的布局复刻：
    - margins = (12, 0, 12, 0)
    - spacing = 8
    - HEADER_BUTTON_SIZE = 36 x 38
    - zoom slider = 90
    - 中央 location / timestamp
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(True)

        row = QHBoxLayout(self)
        row.setContentsMargins(HEADER_MARGIN_X, 0, HEADER_MARGIN_X, 0)
        row.setSpacing(HEADER_SPACING)

        self.back_button = FlatIconButton("‹", parent=self)
        row.addWidget(self.back_button)

        # ----------------------- zoom widget -----------------------
        self.zoom_widget = QWidget(self)
        zoom = QHBoxLayout(self.zoom_widget)
        zoom.setContentsMargins(0, 0, 0, 0)
        zoom.setSpacing(4)

        small = QSize(
            HEADER_BUTTON_SIZE.width() // 2,
            HEADER_BUTTON_SIZE.height() // 2,
        )

        self.zoom_out_button = FlatIconButton("−", parent=self.zoom_widget)
        self.zoom_out_button.setFixedSize(small)
        self.zoom_out_button.setStyleSheet(
            """
            QToolButton {
                border: none;
                background: transparent;
                color: #303030;
                padding: 0px;
                font-size: 15px;
            }
            QToolButton:hover { background: rgba(0,0,0,14); border-radius: 5px; }
            """
        )
        zoom.addWidget(self.zoom_out_button)

        self.zoom_slider = QSlider(
            Qt.Orientation.Horizontal,
            self.zoom_widget,
        )
        self.zoom_slider.setRange(10, 400)
        self.zoom_slider.setSingleStep(5)
        self.zoom_slider.setPageStep(25)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(ZOOM_SLIDER_WIDTH)
        self.zoom_slider.setStyleSheet(
            """
            QSlider::groove:horizontal {
                height: 3px;
                border-radius: 1px;
                background: rgba(0, 0, 0, 28);
            }
            QSlider::sub-page:horizontal {
                height: 3px;
                border-radius: 1px;
                background: rgba(0, 0, 0, 45);
            }
            QSlider::handle:horizontal {
                width: 14px;
                height: 14px;
                margin: -6px 0;
                border-radius: 7px;
                border: 1px solid rgba(0, 0, 0, 55);
                background: white;
            }
            """
        )
        zoom.addWidget(self.zoom_slider)

        self.zoom_in_button = FlatIconButton("+", parent=self.zoom_widget)
        self.zoom_in_button.setFixedSize(small)
        self.zoom_in_button.setStyleSheet(self.zoom_out_button.styleSheet())
        zoom.addWidget(self.zoom_in_button)

        zoom_width = (
            small.width() * 2
            + ZOOM_SLIDER_WIDTH
            + zoom.spacing() * 2
        )
        self.zoom_widget.setMinimumWidth(zoom_width)
        self.zoom_widget.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        row.addWidget(self.zoom_widget)

        # ----------------------- center info -----------------------
        info_container = QWidget(self)
        info = QVBoxLayout(info_container)
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(0)
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.location_label = QLabel("Edinburgh - Queen Street", info_container)
        lf = self.font()
        lf.setPointSize(max(lf.pointSize() + 2, 12))
        lf.setBold(True)
        self.location_label.setFont(lf)
        self.location_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.location_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        self.timestamp_label = QLabel(
            "2025年9月21日 19:49:05    ·    1,918 / 1,931",
            info_container,
        )
        tf = self.font()
        tf.setPointSize(max(tf.pointSize() + 1, 10))
        self.timestamp_label.setFont(tf)
        self.timestamp_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timestamp_label.setStyleSheet("color: rgba(0, 0, 0, 115);")

        info.addWidget(self.location_label)
        info.addWidget(self.timestamp_label)
        row.addWidget(info_container, 1)

        # ----------------------- actions -----------------------
        actions = QWidget(self)
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)

        self.info_button = FlatIconButton("ⓘ", blue=True, parent=actions)
        self.share_button = FlatIconButton("⇧", parent=actions)
        self.favorite_button = FlatIconButton("♡", parent=actions)
        self.rotate_button = FlatIconButton("↶", parent=actions)

        for b in (
            self.info_button,
            self.share_button,
            self.favorite_button,
            self.rotate_button,
        ):
            actions_layout.addWidget(b)

        self.edit_button = QPushButton("编辑", actions)
        self.edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_button.setFixedHeight(30)
        self.edit_button.setStyleSheet(
            """
            QPushButton {
                background-color: palette(window);
                border: 1px solid rgba(0, 0, 0, 30);
                border-radius: 8px;
                color: #000000;
                font-weight: 600;
                padding-left: 20px;
                padding-right: 20px;
            }
            QPushButton:hover { background-color: rgba(0,0,0,12); }
            QPushButton:pressed { background-color: rgba(0,0,0,20); }
            """
        )
        actions_layout.addWidget(self.edit_button)

        row.addWidget(actions)

        # 按 38px hit target，留一点上下空气；视觉接近真实 DetailPage header。
        self.setMinimumHeight(50)
        self.setMaximumHeight(50)


class Filmstrip(QWidget):
    """
    单文件版 filmstrip。
    高度严格复用当前 iPhotron FilmstripView 的 120 + 12 = 132 px。
    """

    def __init__(self, photo_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = QImage(photo_path) if photo_path else QImage()
        self.setFixedHeight(FILMSTRIP_HEIGHT)
        self.setAutoFillBackground(True)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        bg = self.palette().window().color()
        p.fillRect(self.rect(), bg)

        h = FILMSTRIP_BASE_HEIGHT
        gap = 2
        y = 6

        # 当前项目居中；两侧只是复用同一张照片做布局参照。
        current_w = 160
        side_w = 82

        widths = [side_w, side_w, current_w, side_w, side_w]
        total = sum(widths) + gap * (len(widths) - 1)
        x = (self.width() - total) / 2.0

        for i, tw in enumerate(widths):
            r = QRectF(x, y, tw, h)
            if not self._image.isNull():
                self._draw_cover(p, self._image, r)
            else:
                shade = 210 - abs(i - 2) * 18
                p.fillRect(r, QColor(shade, shade, shade))

            if i == 2:
                p.setPen(QPen(QColor("#1e73ff"), 3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), 3, 3)

            x += tw + gap

    @staticmethod
    def _draw_cover(p: QPainter, image: QImage, target: QRectF) -> None:
        iw = image.width()
        ih = image.height()
        if iw <= 0 or ih <= 0:
            return

        target_ratio = target.width() / target.height()
        image_ratio = iw / ih

        if image_ratio > target_ratio:
            crop_h = ih
            crop_w = crop_h * target_ratio
            crop_x = (iw - crop_w) / 2.0
            src = QRectF(crop_x, 0, crop_w, crop_h)
        else:
            crop_w = iw
            crop_h = crop_w / target_ratio
            crop_y = (ih - crop_h) / 2.0
            src = QRectF(0, crop_y, crop_w, crop_h)

        p.drawImage(target, image, src)


class IPhotronPlaybackShadowDemo(QWidget):
    """
    只复刻 iPhotron Detail Playback 区域，不造 sidebar / fake app window。

    直接子控件：
        header
        gl_viewport
        shadow_overlay   <-- sibling，覆盖 gl_viewport 顶部
        filmstrip

    shadow_overlay 不参与 layout，因此不会把照片向下挤 28px。
    """

    HEADER_HEIGHT = 50

    def __init__(self, photo_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(900, 620)
        self.setAutoFillBackground(True)

        self.header = PlaybackHeader(self)
        self.gl_viewport = PhotoGLViewport(photo_path, self)
        self.shadow_overlay = ShadowOverlay(self)
        self.filmstrip = Filmstrip(photo_path, self)

        self.header.zoom_slider.valueChanged.connect(
            self.gl_viewport.set_zoom_percent
        )
        self.header.zoom_out_button.clicked.connect(
            lambda: self.header.zoom_slider.setValue(
                max(
                    self.header.zoom_slider.minimum(),
                    self.header.zoom_slider.value() - 25,
                )
            )
        )
        self.header.zoom_in_button.clicked.connect(
            lambda: self.header.zoom_slider.setValue(
                min(
                    self.header.zoom_slider.maximum(),
                    self.header.zoom_slider.value() + 25,
                )
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)

        w = self.width()
        h = self.height()

        header_h = self.HEADER_HEIGHT
        film_h = FILMSTRIP_HEIGHT
        gap = PLAYER_FILMSTRIP_GAP

        # 真实 toolbar
        self.header.setGeometry(0, 0, w, header_h)

        # GPU viewport：紧贴 toolbar，不为 shadow 预留高度
        viewer_y = header_h
        viewer_h = max(1, h - header_h - gap - film_h)
        self.gl_viewport.setGeometry(0, viewer_y, w, viewer_h)

        # 关键：shadow 是同一 parent 下的 sibling，
        # 直接压在 OpenGL viewport 的 y=0 顶部。
        self.shadow_overlay.setGeometry(
            0,
            viewer_y,
            w,
            min(SHADOW_HEIGHT, viewer_h),
        )
        self.shadow_overlay.raise_()

        self.filmstrip.setGeometry(
            0,
            viewer_y + viewer_h + gap,
            w,
            film_h,
        )
        self.filmstrip.raise_()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # S 键可快速 A/B 对比阴影开/关
        if event.key() == Qt.Key.Key_S:
            self.shadow_overlay.setVisible(
                not self.shadow_overlay.isVisible()
            )
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(
            "iPhotron Playback - toolbar sibling shadow overlay demo"
        )
        self.resize(1440, 900)

        demo = IPhotronPlaybackShadowDemo(PHOTO_PATH, self)
        self.setCentralWidget(demo)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 保持浅色 Photos/iPhotron playback 观察环境。
    pal = app.palette()
    pal.setColor(pal.ColorRole.Window, QColor("#f7f7f7"))
    pal.setColor(pal.ColorRole.Base, QColor("#ffffff"))
    pal.setColor(pal.ColorRole.WindowText, QColor("#1f1f1f"))
    pal.setColor(pal.ColorRole.Text, QColor("#1f1f1f"))
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())