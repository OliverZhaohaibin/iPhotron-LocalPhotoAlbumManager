import sys
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen,
                           QLinearGradient, QFont)
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QLabel, QFrame, QButtonGroup)


# --- 1. 吸管工具 ---
class PipetteButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                background-color: #383838;
                border: 1px solid #555;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #444; }
            QPushButton:pressed { background-color: #222; }
        """)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#bbb"), 1.5)
        painter.setPen(pen)
        painter.drawLine(11, 21, 15, 17)
        painter.drawRect(15, 11, 7, 7)
        painter.drawLine(10, 22, 12, 22)
        painter.drawLine(10, 20, 10, 22)


# --- 2. 颜色选择按钮 ---
class ColorSelectButton(QPushButton):
    def __init__(self, color_hex, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.color = QColor(color_hex)
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        if self.isChecked():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#444444"))
            painter.drawRoundedRect(rect, 4, 4)

        block_size = 12
        center_x = rect.width() / 2
        center_y = rect.height() / 2
        color_rect = QRectF(center_x - block_size / 2, center_y - block_size / 2, block_size, block_size)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self.color)
        painter.drawRoundedRect(color_rect, 3, 3)


# --- 3. 滑块控件 (支持动态更新颜色) ---
class CustomSlider(QWidget):
    valueChanged = Signal(float)

    def __init__(self, name: str, parent=None, minimum=-100, maximum=100, initial=0,
                 bg_start="#2c3e4a", bg_end="#4a3e20",
                 fill_neg="#4a90b4", fill_pos="#b4963c"):
        super().__init__(parent)
        self._name = name
        self._min = float(minimum)
        self._max = float(maximum)
        self._value = float(initial)
        self._dragging = False
        self.setFixedHeight(30)
        self.setCursor(Qt.OpenHandCursor)

        # 初始化颜色
        self.set_colors(bg_start, bg_end, fill_neg, fill_pos)
        self.c_indicator = QColor(255, 255, 255)

    def set_colors(self, bg_start, bg_end, fill_neg, fill_pos):
        """动态更新颜色的方法"""
        self.c_bg_start = QColor(bg_start)
        self.c_bg_end = QColor(bg_end)
        self.c_fill_neg = QColor(fill_neg)
        self.c_fill_pos = QColor(fill_pos)
        self.update()  # 触发重绘

    def _value_to_x(self, val):
        ratio = (val - self._min) / (self._max - self._min)
        return ratio * self.width()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())

        # 1. 背景渐变
        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gradient.setColorAt(0, self.c_bg_start)
        gradient.setColorAt(1, self.c_bg_end)
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        painter.fillPath(path, gradient)

        # 2. 动态颜色填充
        zero_x = self._value_to_x(0) if self._min < 0 else 0
        curr_x = self._value_to_x(self._value)

        current_fill_color = self.c_fill_neg if self._value < 0 else self.c_fill_pos

        if self._min < 0:
            fill_rect = QRectF(min(zero_x, curr_x), 0, abs(curr_x - zero_x), self.height())
        else:
            fill_rect = QRectF(0, 0, curr_x, self.height())

        painter.setOpacity(0.9)
        painter.setClipPath(path)
        painter.fillRect(fill_rect, current_fill_color)
        painter.setClipping(False)
        painter.setOpacity(1.0)

        # 3. 0点分割线
        if self._min < 0 < self._max:
            painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
            painter.drawLine(QPointF(zero_x, 0), QPointF(zero_x, rect.bottom()))

        # 4. 文字
        font = QFont("Segoe UI", 10)
        painter.setFont(font)
        painter.setPen(QColor(230, 230, 230))
        painter.drawText(rect.adjusted(10, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self._name)
        painter.setPen(QColor(255, 255, 255, 200))
        painter.drawText(rect.adjusted(0, 0, -10, 0), Qt.AlignVCenter | Qt.AlignRight, f"{self._value:.2f}")

        # 5. 指示器
        painter.setPen(QPen(self.c_indicator, 2))
        painter.drawLine(QPointF(curr_x, 0), QPointF(curr_x, rect.bottom()))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            self._update_from_pos(event.position().x())

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._update_from_pos(event.position().x())

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self.setCursor(Qt.OpenHandCursor)

    def _update_from_pos(self, x):
        ratio = max(0, min(1, x / self.width()))
        self._value = self._min + ratio * (self._max - self._min)
        self.valueChanged.emit(self._value)
        self.update()


# --- 4. 主面板 ---
class SelectiveColorWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #121212;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Header
        header_layout = QHBoxLayout()
        icon = QLabel(" ∴ ")
        icon.setStyleSheet("color: #d64a6e; font-weight: 900; font-size: 16px;")
        title = QLabel("Selective Color")
        title.setStyleSheet("color: #ddd; font-size: 13px; font-weight: bold;")
        undo_btn = QPushButton("↺")
        undo_btn.setFixedSize(20, 20)
        undo_btn.setStyleSheet("background: transparent; color: #888; border: none;")
        toggle_icon = QLabel("🔵")

        header_layout.addWidget(icon)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(undo_btn)
        header_layout.addWidget(toggle_icon)
        layout.addLayout(header_layout)

        # Tools: Pipette + Colors
        tools_layout = QHBoxLayout()
        tools_layout.setContentsMargins(0, 5, 0, 5)
        tools_layout.setSpacing(0)

        pipette_container = QWidget()
        p_layout = QHBoxLayout(pipette_container)
        p_layout.setContentsMargins(0, 0, 0, 0)
        p_layout.setAlignment(Qt.AlignCenter)
        self.pipette = PipetteButton()
        p_layout.addWidget(self.pipette)

        colors_bg = QFrame()
        colors_bg.setStyleSheet("background-color: #222; border-radius: 6px; border: 1px solid #333;")
        colors_bg.setFixedHeight(34)
        c_layout = QHBoxLayout(colors_bg)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(4)

        # 定义颜色及其ID
        # Red, Yellow, Green, Cyan, Blue, Magenta
        self.color_hexes = ["#FF3B30", "#FFCC00", "#28CD41", "#5AC8FA", "#007AFF", "#AF52DE"]
        self.btn_group = QButtonGroup(self)

        # 监听按钮点击事件
        self.btn_group.idClicked.connect(self.update_theme)

        for i, c_hex in enumerate(self.color_hexes):
            btn = ColorSelectButton(c_hex)
            self.btn_group.addButton(btn, i)
            c_layout.addWidget(btn)

        tools_layout.addWidget(pipette_container, 1)
        tools_layout.addWidget(colors_bg, 0)
        layout.addLayout(tools_layout)

        # Sliders
        self.slider_hue = CustomSlider("Hue")
        self.slider_sat = CustomSlider("Saturation")
        self.slider_lum = CustomSlider("Luminance")
        self.slider_range = CustomSlider("Range", minimum=0, maximum=1.0, initial=1.0,
                                         bg_start="#353535", bg_end="#252525",
                                         fill_neg="#666", fill_pos="#808080")

        layout.addWidget(self.slider_hue)
        layout.addWidget(self.slider_sat)
        layout.addWidget(self.slider_lum)
        layout.addWidget(self.slider_range)
        layout.addStretch()

        # 初始化为第一个颜色 (Red)
        self.btn_group.button(0).setChecked(True)
        self.update_theme(0)

    def update_theme(self, color_idx):
        """根据选中的颜色索引，切换所有滑块的背景样式"""
        base_c = QColor(self.color_hexes[color_idx])

        # 为了让背景不那么刺眼，我们计算一个暗色版本
        dark_base = QColor(base_c)
        dark_base.setAlpha(80)  # 变暗/透明
        bg_dark_hex = dark_base.name()

        # --- 1. Saturation 逻辑 ---
        # 背景：灰 -> 暗色版当前色
        # 左(负)：青灰(Desaturate)
        # 右(正)：当前色(Saturate)
        sat_bg_start = "#4a4a4a"  # 灰色
        sat_bg_end = bg_dark_hex
        sat_fill_neg = "#607080"
        sat_fill_pos = base_c.name()

        # --- 2. Luminance 逻辑 ---
        # 背景：黑 -> 亮色版当前色
        # 左(负)：黑/深灰
        # 右(正)：白/亮灰
        lum_bg_start = "#1a1a1a"
        lum_bg_end = bg_dark_hex
        lum_fill_neg = "#000000"
        lum_fill_pos = "#FFFFFF"

        # --- 3. Hue 逻辑 (复杂：相邻色) ---
        # 定义每个颜色的 [左偏移色, 右偏移色]
        # Red(0): Magenta <-> Yellow
        # Yellow(1): Red <-> Green
        # Green(2): Yellow <-> Cyan
        # Cyan(3): Green <-> Blue
        # Blue(4): Cyan <-> Magenta
        # Magenta(5): Blue <-> Red
        hue_map = {
            0: ("#AF52DE", "#FFCC00"),  # Red -> Mag, Yel
            1: ("#FF3B30", "#28CD41"),  # Yel -> Red, Grn
            2: ("#FFCC00", "#5AC8FA"),  # Grn -> Yel, Cya
            3: ("#28CD41", "#007AFF"),  # Cya -> Grn, Blu
            4: ("#5AC8FA", "#AF52DE"),  # Blu -> Cya, Mag
            5: ("#007AFF", "#FF3B30")  # Mag -> Blu, Red
        }

        left_hue, right_hue = hue_map.get(color_idx, ("#888", "#888"))

        # 背景渐变：左邻色(暗) -> 右邻色(暗)
        c_left = QColor(left_hue)
        c_right = QColor(right_hue)
        c_left.setAlpha(100)
        c_right.setAlpha(100)

        hue_bg_start = c_left.name(QColor.HexArgb)
        hue_bg_end = c_right.name(QColor.HexArgb)

        hue_fill_neg = left_hue
        hue_fill_pos = right_hue

        # 应用更新
        self.slider_hue.set_colors(hue_bg_start, hue_bg_end, hue_fill_neg, hue_fill_pos)
        self.slider_sat.set_colors(sat_bg_start, sat_bg_end, sat_fill_neg, sat_fill_pos)
        self.slider_lum.set_colors(lum_bg_start, lum_bg_end, lum_fill_neg, lum_fill_pos)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 9))

    win = QWidget()
    win.setWindowTitle("Selective Color UI")
    win.setFixedWidth(320)
    win.setStyleSheet("background-color: #121212;")

    main_layout = QVBoxLayout(win)
    main_layout.setContentsMargins(0, 0, 0, 0)
    main_layout.addWidget(SelectiveColorWidget())
    main_layout.addStretch()

    win.show()
    sys.exit(app.exec())