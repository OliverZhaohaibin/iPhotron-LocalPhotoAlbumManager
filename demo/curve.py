import sys
import os
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QComboBox, QPushButton, QLabel, QFrame)
from PySide6.QtCore import Qt, QPointF, QSize
from PySide6.QtGui import (QPainter, QColor, QPen, QPainterPath, QIcon)

# ==========================================
# 配置：图标路径 (请确保这些文件存在)
# ==========================================
ICON_PATH_BLACK = r"D:\python_code\iPhoto\iPhotos\src\iPhoto\gui\ui\icon\eyedropper.full.svg"
ICON_PATH_GRAY = r"D:\python_code\iPhoto\iPhotos\src\iPhoto\gui\ui\icon\eyedropper.halffull.svg"
ICON_PATH_WHITE = r"D:\python_code\iPhoto\iPhotos\src\iPhoto\gui\ui\icon\eyedropper.svg"


# ==========================================
# 1. 自定义下拉框 (已修复崩溃问题)
# ==========================================
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
            /* 隐藏系统默认箭头，防止重叠 */
            QComboBox::down-arrow {
                image: none;
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #383838;
                color: white;
                selection-background-color: #505050;
                border: 1px solid #555;
            }
        """)

    def paintEvent(self, event):
        # 1. 先让父类画好背景和文字
        super().paintEvent(event)

        # 2. 再单独画我们的蓝色箭头
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        arrow_color = QColor("#4a90e2")  # 亮蓝色
        rect = self.rect()

        # 计算箭头位置
        cx = rect.width() - 15
        cy = rect.height() / 2
        size = 4

        p1 = QPointF(cx - size, cy - size / 2)
        p2 = QPointF(cx, cy + size / 2)
        p3 = QPointF(cx + size, cy - size / 2)

        pen = QPen(arrow_color, 2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(p1, p2)
        painter.drawLine(p2, p3)

        painter.end()


# ==========================================
# 2. 自定义曲线编辑控件 (含单调性约束)
# ==========================================
class CurveGraph(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self.setStyleSheet("background-color: #2b2b2b;")
        self.points = [QPointF(0.0, 0.0), QPointF(1.0, 1.0)]
        self.selected_index = -1
        self.dragging = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 背景与网格
        painter.fillRect(self.rect(), QColor("#222222"))
        painter.setPen(QPen(QColor("#444444"), 1))
        for i in range(1, 4):
            painter.drawLine(i * w / 4, 0, i * w / 4, h)
            painter.drawLine(0, i * h / 4, w, i * h / 4)

        # 模拟直方图
        self.draw_fake_histogram(painter, w, h)
        # 曲线
        self.draw_curve(painter, w, h)

        # 控制点
        point_radius = 5
        for i, p in enumerate(self.points):
            sx = p.x() * w
            sy = h - (p.y() * h)

            if i == self.selected_index:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor("white"), 2))
                painter.drawEllipse(QPointF(sx, sy), point_radius + 2, point_radius + 2)

            painter.setBrush(QColor("#ffffff"))
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.drawEllipse(QPointF(sx, sy), point_radius, point_radius)

    def draw_fake_histogram(self, painter, w, h):
        path = QPainterPath()
        path.moveTo(0, h)
        import math
        step = 4
        for x in range(0, w + step, step):
            nx = x / w
            # 生成两个高斯波峰模拟直方图
            val = math.exp(-((nx - 0.35) ** 2) / 0.04) * 0.6 + math.exp(-((nx - 0.75) ** 2) / 0.08) * 0.4
            # 添加一点随机抖动 (用正弦模拟)
            noise = math.sin(x * 0.1) * 0.05
            h_val = (val + abs(noise)) * h * 0.8
            path.lineTo(x, h - h_val)
        path.lineTo(w, h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(120, 120, 120, 60))
        painter.drawPath(path)

    def draw_curve(self, painter, w, h):
        if len(self.points) < 2: return
        screen_pts = [QPointF(p.x() * w, h - p.y() * h) for p in self.points]

        path = QPainterPath()
        path.moveTo(screen_pts[0])
        for i in range(len(screen_pts) - 1):
            p0, p1 = screen_pts[i], screen_pts[i + 1]
            dx = p1.x() - p0.x()
            path.cubicTo(QPointF(p0.x() + dx * 0.5, p0.y()), QPointF(p1.x() - dx * 0.5, p1.y()), p1)

        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def mousePressEvent(self, event):
        pos = event.position()
        w, h = self.width(), self.height()
        click_idx = -1

        # 判定点击
        for i, p in enumerate(self.points):
            sx, sy = p.x() * w, h - p.y() * h
            if (pos.x() - sx) ** 2 + (pos.y() - sy) ** 2 < 225:  # 15*15
                click_idx = i
                break

        if click_idx != -1:
            self.selected_index = click_idx
        else:
            # 加点
            nx = max(0.0, min(1.0, pos.x() / w))
            ny = max(0.0, min(1.0, (h - pos.y()) / h))
            insert_i = len(self.points)
            for i, p in enumerate(self.points):
                if p.x() > nx:
                    insert_i = i
                    break
            self.points.insert(insert_i, QPointF(nx, ny))
            self.selected_index = insert_i
            self.constrain_point(insert_i)

        self.dragging = True
        self.update()

    def mouseMoveEvent(self, event):
        if self.dragging and self.selected_index != -1:
            pos = event.position()
            w, h = self.width(), self.height()
            nx = max(0.0, min(1.0, pos.x() / w))
            ny = max(0.0, min(1.0, (h - pos.y()) / h))

            # 锁定首尾X
            if self.selected_index == 0:
                nx = 0.0
            elif self.selected_index == len(self.points) - 1:
                nx = 1.0

            self.points[self.selected_index] = QPointF(nx, ny)
            self.constrain_point(self.selected_index)
            self.update()

    def mouseReleaseEvent(self, event):
        self.dragging = False

    def constrain_point(self, idx):
        # 强制单调递增
        cx, cy = self.points[idx].x(), self.points[idx].y()
        if idx > 0:
            prev = self.points[idx - 1]
            if cx <= prev.x() + 0.01: cx = prev.x() + 0.01
            if cy < prev.y(): cy = prev.y()
        if idx < len(self.points) - 1:
            next_p = self.points[idx + 1]
            if cx >= next_p.x() - 0.01: cx = next_p.x() - 0.01
            if cy > next_p.y(): cy = next_p.y()
        self.points[idx] = QPointF(cx, cy)


# ==========================================
# 3. 工具栏按钮 (支持 SVG 图标)
# ==========================================
class IconButton(QPushButton):
    def __init__(self, icon_path, tooltip, parent=None):
        super().__init__(parent)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 30)

        # 检查文件是否存在，防止空白
        if os.path.exists(icon_path):
            self.setIcon(QIcon(icon_path))
            self.setIconSize(QSize(20, 20))  # 设置合适的图标大小
        else:
            self.setText("?")  # 如果找不到图标，显示问号
            print(f"Warning: Icon not found at {icon_path}")

        self.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 0px;
            }
            QPushButton:hover {
                background-color: #444;
            }
            QPushButton:pressed {
                background-color: #222;
            }
        """)


# ==========================================
# 4. 主窗口
# ==========================================
class CurvesDemo(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Curves Demo")
        self.setStyleSheet("background-color: #1e1e1e; font-family: 'Segoe UI', sans-serif;")
        self.setFixedSize(400, 520)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 顶部标题栏
        top_bar = QHBoxLayout()
        title = QLabel("Curves")
        title.setStyleSheet("color: #ddd; font-weight: bold; font-size: 14px;")
        auto_btn = QPushButton("AUTO")
        auto_btn.setFixedSize(50, 20)
        auto_btn.setStyleSheet(
            "QPushButton { background-color: #333; color: #aaa; border-radius: 10px; border: 1px solid #555; font-size: 10px; }")
        top_bar.addWidget(title)
        top_bar.addStretch()
        top_bar.addWidget(auto_btn)
        main_layout.addLayout(top_bar)

        # 通道下拉框
        combo = StyledComboBox()
        combo.addItems(["RGB", "Red", "Green", "Blue"])
        main_layout.addWidget(combo)

        # 工具栏容器
        tools_layout = QHBoxLayout()
        tools_frame = QFrame()
        tools_frame.setStyleSheet(".QFrame { background-color: #383838; border-radius: 5px; border: 1px solid #555; }")
        tf_layout = QHBoxLayout(tools_frame)
        tf_layout.setContentsMargins(0, 0, 0, 0)
        tf_layout.setSpacing(0)

        # === 创建三个吸管按钮 ===
        # 使用你提供的路径
        btn_black = IconButton(ICON_PATH_BLACK, "Set Black Point")
        btn_gray = IconButton(ICON_PATH_GRAY, "Set Gray Point")
        btn_white = IconButton(ICON_PATH_WHITE, "Set White Point")

        # 添加中间分割线样式
        border_style = "border-right: 1px solid #555;"
        btn_black.setStyleSheet(btn_black.styleSheet() + border_style)
        btn_gray.setStyleSheet(btn_gray.styleSheet() + border_style)
        # 最后一个不需要右边框

        tf_layout.addWidget(btn_black)
        tf_layout.addWidget(btn_gray)
        tf_layout.addWidget(btn_white)
        tools_layout.addWidget(tools_frame)

        # 右侧的那个“添加点”按钮 (手型/十字)
        btn_hand = QPushButton("✚")  # 这里如果没有svg，暂时还是用字符，或者你可以换成对应svg
        btn_hand.setFixedSize(40, 32)
        btn_hand.setStyleSheet("""
            QPushButton { background-color: #383838; border: 1px solid #555; border-radius: 4px; color: #aaa; font-size: 16px; }
            QPushButton:hover { background-color: #444; }
        """)
        tools_layout.addSpacing(5)
        tools_layout.addWidget(btn_hand)
        tools_layout.addStretch()
        main_layout.addLayout(tools_layout)

        # 曲线编辑器
        self.curve = CurveGraph()
        main_layout.addWidget(self.curve)

        # 底部渐变条
        grad_bar = QFrame()
        grad_bar.setFixedHeight(15)
        grad_bar.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 black, stop:1 white); border-radius: 2px;")
        main_layout.addWidget(grad_bar)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = CurvesDemo()
    win.show()
    sys.exit(app.exec())