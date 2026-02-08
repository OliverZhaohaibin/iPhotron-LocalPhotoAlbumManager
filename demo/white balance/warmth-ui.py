import sys
from typing import Optional
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen,
                           QLinearGradient, QFont, QBrush)
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QComboBox, QLabel, QFrame, QAbstractItemView)


# 1. 继承并使用你提供的下拉框样式
class StyledComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QComboBox {
                background-color: #383838;
                color: white;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 13px;
                border: 1px solid #555;
            }
            QComboBox::drop-down {
                border: 0px;
                width: 25px;
            }
            QComboBox::down-arrow {
                image: none;
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #383838;
                color: white;
                selection-background-color: #505050;
                border: 1px solid #555;
                outline: 0px;
            }
        """)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 绘制你指定的蓝色 V 型箭头
        arrow_color = QColor("#4a90e2")
        rect = self.rect()
        cx = rect.width() - 15
        cy = rect.height() / 2
        size = 4

        # 绘制双向/单向箭头的逻辑 (这里改为类似截图的上下小箭头)
        p1 = QPointF(cx - size, cy - 2)
        p2 = QPointF(cx, cy - 6)
        p3 = QPointF(cx + size, cy - 2)

        p4 = QPointF(cx - size, cy + 2)
        p5 = QPointF(cx, cy + 6)
        p6 = QPointF(cx + size, cy + 2)

        pen = QPen(arrow_color, 2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        # 上箭头
        painter.drawPolyline([p1, p2, p3])
        # 下箭头
        painter.drawPolyline([p4, p5, p6])
        painter.end()


# 2. 吸管工具按钮
class PipetteButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                background-color: #383838;
                border: 1px solid #555;
                border-radius: 6px;
                color: #ddd;
            }
            QPushButton:hover { background-color: #444; }
        """)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 绘制一个简单的吸管图标
        pen = QPen(QColor("#bbb"), 1.5)
        painter.setPen(pen)
        painter.drawLine(12, 20, 16, 16)
        painter.drawRect(16, 10, 8, 8)


# 3. 核心：带刻度和渐变的 Warmth 滑块
class WarmthSlider(QWidget):
    valueChanged = Signal(float)

    def __init__(self, name: str = "Warmth", parent=None, minimum=-100, maximum=100, initial=0):
        super().__init__(parent)
        self._name = name
        self._min = float(minimum)
        self._max = float(maximum)
        self._value = float(initial)
        self._dragging = False

        self.setFixedHeight(34)
        self.setCursor(Qt.OpenHandCursor)

        # 颜色配置
        self.c_blue_track = QColor(44, 62, 74)  # 冷色调背景
        self.c_orange_track = QColor(74, 62, 32)  # 暖色调背景

        # 高亮填充色：取背景色的明度稍高版本
        self.c_fill_blue = QColor(74, 144, 180)  # 负值时的蓝色填充
        self.c_fill_warm = QColor(180, 150, 60)  # 正值时的暖色填充

        self.c_indicator = QColor(255, 255, 255)  # 白色指示线
        self.c_tick = QColor(255, 255, 255, 60)  # 保持原有的半透明刻度

    def _value_to_x(self, val):
        ratio = (val - self._min) / (self._max - self._min)
        return ratio * self.width()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())

        # --- 1. 绘制原始渐变背景 ---
        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gradient.setColorAt(0, self.c_blue_track)
        gradient.setColorAt(1, self.c_orange_track)
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        painter.fillPath(path, gradient)

        # --- 2. 绘制从 0 到当前值的填充块 (核心新增) ---
        zero_x = self._value_to_x(0)
        curr_x = self._value_to_x(self._value)

        fill_color = self.c_fill_blue if self._value < 0 else self.c_fill_warm
        fill_rect = QRectF(min(zero_x, curr_x), 0, abs(curr_x - zero_x), self.height())
        # 使用较轻的透明度让它看起来像是在背景上“亮起”
        painter.setOpacity(0.8)
        painter.fillRect(fill_rect, fill_color)
        painter.setOpacity(1.0)

        # --- 3. 绘制原始顶部刻度线 (不改样式，无底部刻度) ---
        painter.setPen(QPen(self.c_tick, 1))
        ticks = 50
        for i in range(ticks + 1):
            x = (i / ticks) * rect.width()
            h = 6 if i % 5 == 0 else 3
            painter.drawLine(QPointF(x, 0), QPointF(x, h))

        # --- 4. 绘制中心 0 点白线 (辅助对齐) ---
        painter.setPen(QPen(QColor(255, 255, 255, 100), 1))
        painter.drawLine(QPointF(zero_x, 0), QPointF(zero_x, rect.bottom()))

        # --- 5. 绘制文字 (Warmth & 数值) ---
        font = QFont("Inter", 12, QFont.Weight.Medium)
        painter.setFont(font)
        # 左侧名称
        painter.setPen(QColor(240, 240, 240))
        painter.drawText(rect.adjusted(12, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self._name)
        # 右侧数值 (模仿截图显示小数形式)
        painter.setPen(QColor(255, 255, 255, 200))
        val_display = f"{self._value / 100:.2f}"
        painter.drawText(rect.adjusted(0, 0, -12, 0), Qt.AlignVCenter | Qt.AlignRight, val_display)

        # --- 6. 绘制白色指示线 ---
        painter.setPen(QPen(self.c_indicator, 2))
        painter.drawLine(QPointF(curr_x, 0), QPointF(curr_x, rect.bottom()))

    # 交互部分保持不变
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
# 4. 组合面板
class WhiteBalanceWidget(QWidget):
    def __init__(self):
        super().__init__()
        # 这里已经是深色了 (#1a1a1a)，保持不变
        self.setStyleSheet("background-color: #1a1a1a;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # Header: White Balance, Auto, Check
        header = QHBoxLayout()
        icon_lbl = QLabel("⚖️")
        title_lbl = QLabel("White Balance")
        title_lbl.setStyleSheet(
            "color: #eee; font-weight: bold; font-size: 14px; border: none; background: transparent;")

        auto_btn = QPushButton("AUTO")
        auto_btn.setFixedSize(50, 20)
        auto_btn.setStyleSheet("""
            QPushButton { 
                background-color: #333; color: #999; border: 1px solid #444; 
                border-radius: 10px; font-size: 9px; font-weight: bold;
            }
        """)

        check_btn = QPushButton("✓")
        check_btn.setFixedSize(22, 22)
        check_btn.setStyleSheet("background-color: #007aff; border-radius: 11px; color: black; font-weight: bold;")

        header.addWidget(icon_lbl)
        header.addWidget(title_lbl)
        header.addStretch()
        header.addWidget(auto_btn)
        header.addWidget(check_btn)

        # Middle: Pipette + StyledComboBox
        tool_row = QHBoxLayout()
        self.pipette = PipetteButton()
        self.combo = StyledComboBox()
        self.combo.addItems(["Neutral Gray", "Skin Tone", "Temp & Tint"])

        tool_row.addWidget(self.pipette)
        tool_row.addWidget(self.combo, 1)

        # Bottom: Slider
        self.slider = WarmthSlider("Warmth")

        layout.addLayout(header)
        layout.addLayout(tool_row)
        layout.addWidget(self.slider)


# --- 程序入口 ---
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 全局字体微调
    font = QFont("Segoe UI", 9)
    app.setFont(font)

    main_win = QWidget()
    main_win.setWindowTitle("WB Edit Sidebar")
    main_win.setFixedWidth(320)

    # --- 修改处：设置主窗口背景为深色 (#121212) ---
    # 这样主窗口四周的填充部分也会变成黑色，而不是系统的灰/白色
    main_win.setStyleSheet("background-color: #121212;")

    main_layout = QVBoxLayout(main_win)
    main_layout.addWidget(WhiteBalanceWidget())
    main_layout.addStretch()

    main_win.show()
    sys.exit(app.exec())