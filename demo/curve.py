import sys
import os
import numpy as np
from PIL import Image

try:
    from numba import jit
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False
    # Fallback dummy decorator
    def jit(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

try:
    from demo.spline import MonotoneCubicSpline
except ImportError:
    # Try local import if running as script from root
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from demo.spline import MonotoneCubicSpline


from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QComboBox, QPushButton, QLabel, QFrame, QSizePolicy, QFileDialog)
from PySide6.QtCore import Qt, QPointF, QSize, Signal
from PySide6.QtGui import (QPainter, QColor, QPen, QPainterPath, QIcon, QSurfaceFormat)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL import GL as gl

# ==========================================
# 配置：图标路径
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(BASE_DIR, "../src")):
    SRC_DIR = os.path.join(BASE_DIR, "../src")
elif os.path.exists(os.path.join(BASE_DIR, "src")):
    SRC_DIR = os.path.join(BASE_DIR, "src")
else:
    SRC_DIR = "src"

ICON_BASE = os.path.join(SRC_DIR, "iPhoto/gui/ui/icon")

ICON_PATH_BLACK = os.path.join(ICON_BASE, "eyedropper.full.svg")
ICON_PATH_GRAY = os.path.join(ICON_BASE, "eyedropper.halffull.svg")
ICON_PATH_WHITE = os.path.join(ICON_BASE, "eyedropper.svg")
ICON_PATH_ADD = os.path.join(ICON_BASE, "circle.cross.svg")


# ==========================================
# Optimized Histogram Calculation
# ==========================================
@jit(nopython=True)
def calculate_histogram_numba(img_array):
    # img_array: (H, W, 3) or (H, W, 4) uint8
    # Returns: (3, 256) float32 normalized
    h, w = img_array.shape[:2]
    c = img_array.shape[2]

    hist = np.zeros((3, 256), dtype=np.float32)

    # We only care about R, G, B (0, 1, 2)
    for y in range(h):
        for x in range(w):
            r = img_array[y, x, 0]
            g = img_array[y, x, 1]
            b = img_array[y, x, 2]
            hist[0, r] += 1
            hist[1, g] += 1
            hist[2, b] += 1

    # Normalize by max value across all channels for consistent scaling
    # Or per channel? Usually global max is better to see relative distribution.
    max_val = 0.0
    for i in range(3):
        for j in range(256):
            if hist[i, j] > max_val:
                max_val = hist[i, j]

    if max_val > 0:
        hist /= max_val

    return hist

def calculate_histogram_numpy(img_array):
    # Fallback if numba fails or not available (though we ensured it is)
    # img_array: (H, W, C)
    hist = np.zeros((3, 256), dtype=np.float32)
    for i in range(3):
        h_data, _ = np.histogram(img_array[:, :, i], bins=256, range=(0, 256))
        hist[i] = h_data

    max_val = hist.max()
    if max_val > 0:
        hist /= max_val
    return hist


# ==========================================
# OpenGL Shaders
# ==========================================
VERTEX_SHADER_SOURCE = """#version 330 core
layout (location = 0) in vec2 aPos;
layout (location = 1) in vec2 aTexCoord;

out vec2 TexCoord;

void main() {
    gl_Position = vec4(aPos, 0.0, 1.0);
    TexCoord = vec2(aTexCoord.x, 1.0 - aTexCoord.y);
}
"""

FRAGMENT_SHADER_SOURCE = """#version 330 core
out vec4 FragColor;

in vec2 TexCoord;

uniform sampler2D imageTexture;
uniform sampler1D curveLUT;
uniform bool hasImage;

void main() {
    if (!hasImage) {
        FragColor = vec4(0.15, 0.15, 0.15, 1.0);
        return;
    }

    vec4 col = texture(imageTexture, TexCoord);

    // LUT lookup
    float r = texture(curveLUT, col.r).r;
    float g = texture(curveLUT, col.g).r;
    float b = texture(curveLUT, col.b).r;

    FragColor = vec4(r, g, b, col.a);
}
"""


# ==========================================
# GL Image Viewer
# ==========================================
class GLImageViewer(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image_texture = None
        self.lut_texture = None
        self.shader_program = None
        self.vao = None
        self.vbo = None
        self.has_image = False
        self._pending_lut = None

    def initializeGL(self):
        gl.glClearColor(0.15, 0.15, 0.15, 1.0)

        vertex_shader = gl.glCreateShader(gl.GL_VERTEX_SHADER)
        gl.glShaderSource(vertex_shader, VERTEX_SHADER_SOURCE)
        gl.glCompileShader(vertex_shader)
        if not gl.glGetShaderiv(vertex_shader, gl.GL_COMPILE_STATUS):
            print(f"Vertex Shader Error: {gl.glGetShaderInfoLog(vertex_shader)}")

        fragment_shader = gl.glCreateShader(gl.GL_FRAGMENT_SHADER)
        gl.glShaderSource(fragment_shader, FRAGMENT_SHADER_SOURCE)
        gl.glCompileShader(fragment_shader)
        if not gl.glGetShaderiv(fragment_shader, gl.GL_COMPILE_STATUS):
            print(f"Fragment Shader Error: {gl.glGetShaderInfoLog(fragment_shader)}")

        self.shader_program = gl.glCreateProgram()
        gl.glAttachShader(self.shader_program, vertex_shader)
        gl.glAttachShader(self.shader_program, fragment_shader)
        gl.glLinkProgram(self.shader_program)
        if not gl.glGetProgramiv(self.shader_program, gl.GL_LINK_STATUS):
            print(f"Link Error: {gl.glGetProgramInfoLog(self.shader_program)}")

        gl.glDeleteShader(vertex_shader)
        gl.glDeleteShader(fragment_shader)

        vertices = np.array([
            -1.0, -1.0,   0.0, 0.0,
             1.0, -1.0,   1.0, 0.0,
             1.0,  1.0,   1.0, 1.0,
            -1.0,  1.0,   0.0, 1.0
        ], dtype=np.float32)

        self.vao = gl.glGenVertexArrays(1)
        self.vbo = gl.glGenBuffers(1)

        gl.glBindVertexArray(self.vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self.vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, vertices.nbytes, vertices, gl.GL_STATIC_DRAW)

        gl.glVertexAttribPointer(0, 2, gl.GL_FLOAT, gl.GL_FALSE, 4 * 4, None)
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(1, 2, gl.GL_FLOAT, gl.GL_FALSE, 4 * 4, gl.ctypes.c_void_p(2 * 4))
        gl.glEnableVertexAttribArray(1)

        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
        gl.glBindVertexArray(0)

        if self._pending_lut is not None:
             self.upload_lut(self._pending_lut)
        else:
             self.upload_lut(np.linspace(0, 1, 256, dtype=np.float32))

    def upload_lut(self, lut_data):
        if not self.isValid():
            self._pending_lut = lut_data
            return

        self.makeCurrent()
        if self.lut_texture is None:
            self.lut_texture = gl.glGenTextures(1)

        gl.glBindTexture(gl.GL_TEXTURE_1D, self.lut_texture)
        gl.glTexParameteri(gl.GL_TEXTURE_1D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_1D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_1D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)

        gl.glTexImage1D(gl.GL_TEXTURE_1D, 0, gl.GL_RED, 256, 0, gl.GL_RED, gl.GL_FLOAT, lut_data)
        gl.glBindTexture(gl.GL_TEXTURE_1D, 0)
        self.doneCurrent()
        self.update()

    def set_image(self, img_pil):
        self.makeCurrent()
        img = img_pil.convert("RGBA")
        data = np.array(img)
        w, h = img.size

        if self.image_texture is None:
            self.image_texture = gl.glGenTextures(1)

        gl.glBindTexture(gl.GL_TEXTURE_2D, self.image_texture)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)

        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA, w, h, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, data)
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)

        self.has_image = True
        self.doneCurrent()
        self.update()

    def paintGL(self):
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        if self.shader_program:
            gl.glUseProgram(self.shader_program)

            if self.has_image:
                gl.glActiveTexture(gl.GL_TEXTURE0)
                gl.glBindTexture(gl.GL_TEXTURE_2D, self.image_texture)
                gl.glUniform1i(gl.glGetUniformLocation(self.shader_program, "imageTexture"), 0)
                gl.glUniform1i(gl.glGetUniformLocation(self.shader_program, "hasImage"), 1)
            else:
                gl.glUniform1i(gl.glGetUniformLocation(self.shader_program, "hasImage"), 0)

            if self.lut_texture:
                gl.glActiveTexture(gl.GL_TEXTURE1)
                gl.glBindTexture(gl.GL_TEXTURE_1D, self.lut_texture)
                gl.glUniform1i(gl.glGetUniformLocation(self.shader_program, "curveLUT"), 1)

            gl.glBindVertexArray(self.vao)
            gl.glDrawArrays(gl.GL_TRIANGLE_FAN, 0, 4)
            gl.glBindVertexArray(0)
            gl.glUseProgram(0)


# ==========================================
# UI Classes
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
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        arrow_color = QColor("#4a90e2")
        rect = self.rect()
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


class CurveGraph(QWidget):
    lutChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self.setStyleSheet("background-color: #2b2b2b;")

        # 2.1 Initialization State: Linear identity (0,0)->(1,1)
        self.points = [QPointF(0.0, 0.0), QPointF(1.0, 1.0)]
        self.selected_index = -1
        self.dragging = False
        self.histogram_data = None
        self.spline = None
        self._recalculate_spline()

    def set_histogram(self, hist_data):
        self.histogram_data = hist_data
        self.update()

    def _recalculate_spline(self):
        # Sort points by x just in case, though logic should maintain order
        self.points.sort(key=lambda p: p.x())

        x = [p.x() for p in self.points]
        y = [p.y() for p in self.points]
        self.spline = MonotoneCubicSpline(x, y)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), QColor("#222222"))

        # Grid
        painter.setPen(QPen(QColor("#444444"), 1))
        for i in range(1, 4):
            painter.drawLine(i * w / 4, 0, i * w / 4, h)
            painter.drawLine(0, i * h / 4, w, i * h / 4)

        if self.histogram_data is not None:
            self.draw_real_histogram(painter, w, h)
        else:
            self.draw_fake_histogram(painter, w, h)

        self.draw_curve(painter, w, h)

        point_radius = 5
        for i, p in enumerate(self.points):
            sx = p.x() * w
            sy = h - (p.y() * h)

            # Visual Feedback: Hollow for selected
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
            val = math.exp(-((nx - 0.35) ** 2) / 0.04) * 0.6 + math.exp(-((nx - 0.75) ** 2) / 0.08) * 0.4
            noise = math.sin(x * 0.1) * 0.05
            h_val = (val + abs(noise)) * h * 0.8
            path.lineTo(x, h - h_val)
        path.lineTo(w, h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(120, 120, 120, 60))
        painter.drawPath(path)

    def draw_real_histogram(self, painter, w, h):
        if len(self.histogram_data.shape) == 1:
            self._draw_hist_channel(painter, self.histogram_data, QColor(120, 120, 120, 128), w, h)
        elif self.histogram_data.shape[0] == 3:
            self._draw_hist_channel(painter, self.histogram_data[0], QColor(255, 0, 0, 100), w, h)
            self._draw_hist_channel(painter, self.histogram_data[1], QColor(0, 255, 0, 100), w, h)
            self._draw_hist_channel(painter, self.histogram_data[2], QColor(0, 0, 255, 100), w, h)

    def _draw_hist_channel(self, painter, data, color, w, h):
        path = QPainterPath()
        path.moveTo(0, h)
        bin_width = w / 256.0
        for i, val in enumerate(data):
            x = i * bin_width
            y = h - (val * h * 0.9)
            if i == 0:
                path.lineTo(x, y)
            path.lineTo((i+0.5) * bin_width, y)
        path.lineTo(w, h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPath(path)

    def draw_curve(self, painter, w, h):
        if self.spline is None:
            return

        # 3.1 Data Separation & Rendering: Sample spline for polyline
        # Using ~1 sample per pixel or every 2 pixels
        steps = w // 2
        if steps < 100: steps = 100

        xs = np.linspace(0, 1, steps)
        ys = self.spline.evaluate(xs)

        path = QPainterPath()
        path.moveTo(xs[0] * w, h - ys[0] * h)

        for i in range(1, len(xs)):
            cx = xs[i] * w
            cy = h - ys[i] * h
            path.lineTo(cx, cy)

        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def mousePressEvent(self, event):
        pos = event.position()
        w, h = self.width(), self.height()

        # Hit detection radius
        hit_radius_sq = 15 * 15

        click_idx = -1
        min_dist_sq = float('inf')

        # Find closest point
        for i, p in enumerate(self.points):
            sx, sy = p.x() * w, h - p.y() * h
            dist_sq = (pos.x() - sx) ** 2 + (pos.y() - sy) ** 2
            if dist_sq < hit_radius_sq:
                if dist_sq < min_dist_sq:
                    min_dist_sq = dist_sq
                    click_idx = i

        if click_idx != -1:
            self.selected_index = click_idx
            # Delete point on right click if not endpoint
            if event.button() == Qt.RightButton and 0 < click_idx < len(self.points) - 1:
                 self.points.pop(click_idx)
                 self.selected_index = -1
                 self.update_spline_and_lut()
        else:
            # If not clicking a point, maybe adding one?
            # Requirement says "Add Point" button logic is specific.
            # But standard curve editors also allow click-to-add on the line.
            # Preserving existing click-to-add logic but using spline evaluation?
            # Or just doing nothing as per "Add Point Button Logic" focus?
            # The original code added a point. I will keep it for better UX,
            # but strictly constrain it.

            # Map screen to 0..1
            nx = max(0.0, min(1.0, pos.x() / w))
            # Find where to insert
            insert_i = len(self.points)
            for i, p in enumerate(self.points):
                if p.x() > nx:
                    insert_i = i
                    break

            # Use spline to get exact Y at this X if we were snapping to curve,
            # but user clicked a specific Y.
            # Requirement 2.4 is about "Add Point Button".
            # Requirement 2.3 says "Add Point: Users must be able to add points".

            ny = max(0.0, min(1.0, (h - pos.y()) / h))

            # Enforce constraints relative to neighbors
            prev_x = self.points[insert_i-1].x() if insert_i > 0 else 0
            next_x = self.points[insert_i].x() if insert_i < len(self.points) else 1

            # Minimal separation
            if nx > prev_x + 0.01 and nx < next_x - 0.01:
                self.points.insert(insert_i, QPointF(nx, ny))
                self.selected_index = insert_i
                self.update_spline_and_lut()

        self.dragging = True
        self.update()

    def mouseMoveEvent(self, event):
        if self.dragging and self.selected_index != -1:
            pos = event.position()
            w, h = self.width(), self.height()

            nx = max(0.0, min(1.0, pos.x() / w))
            ny = max(0.0, min(1.0, (h - pos.y()) / h))

            # 2.3 Drag Constraints:
            # Endpoints clamped to x=0 and x=1
            if self.selected_index == 0:
                nx = 0.0
            elif self.selected_index == len(self.points) - 1:
                nx = 1.0
            else:
                # Neighbors constraint: x_{i-1} < x_i < x_{i+1}
                prev_p = self.points[self.selected_index - 1]
                next_p = self.points[self.selected_index + 1]

                min_x = prev_p.x() + 0.01
                max_x = next_p.x() - 0.01

                if nx < min_x: nx = min_x
                if nx > max_x: nx = max_x

            self.points[self.selected_index] = QPointF(nx, ny)

            # 3.2 Performance: Recalculate real-time
            self.update_spline_and_lut()

    def mouseReleaseEvent(self, event):
        self.dragging = False

    def add_point_smart(self):
        """
        2.4 "Add Point" Button Logic
        """
        if len(self.points) < 2: return

        # 1. Identify gap with max horizontal distance
        max_gap = -1.0
        max_gap_idx = -1

        for i in range(len(self.points) - 1):
            gap = self.points[i+1].x() - self.points[i].x()
            if gap > max_gap:
                max_gap = gap
                max_gap_idx = i

        if max_gap_idx != -1:
            # 2. Insert at geometric midpoint
            p0 = self.points[max_gap_idx]
            p1 = self.points[max_gap_idx + 1]
            mid_x = p0.x() + (p1.x() - p0.x()) * 0.5

            # 3. Y value based on CURRENT curve value
            # Since shape shouldn't change, we evaluate existing spline
            if self.spline:
                mid_y = self.spline.evaluate(mid_x)
            else:
                # Fallback linear if no spline (shouldn't happen)
                mid_y = p0.y() + (p1.y() - p0.y()) * 0.5

            # Clamp Y
            mid_y = max(0.0, min(1.0, mid_y))

            new_point = QPointF(mid_x, mid_y)
            insert_at = max_gap_idx + 1
            self.points.insert(insert_at, new_point)

            self.selected_index = insert_at
            self.update_spline_and_lut()

    # Alias for backward compatibility if needed, or update connection
    def add_point_center(self):
        self.add_point_smart()

    def update_spline_and_lut(self):
        self._recalculate_spline()
        self.update_lut()
        self.update()

    def update_lut(self):
        if not self.spline:
             return

        # 3.1 Data (LUT): Sample exact same function
        # Resolution: 256
        xs = np.linspace(0, 1, 256)
        lut = self.spline.evaluate(xs)

        # Clip to valid range 0..1
        lut = np.clip(lut, 0.0, 1.0)

        self.lutChanged.emit(lut.astype(np.float32))


class IconButton(QPushButton):
    def __init__(self, icon_path, tooltip, parent=None):
        super().__init__(parent)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 30)
        if os.path.exists(icon_path):
            self.setIcon(QIcon(icon_path))
            self.setIconSize(QSize(20, 20))
        else:
            self.setText("?")
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


class CurvesDemo(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Curves Demo")
        self.setStyleSheet("background-color: #1e1e1e; font-family: 'Segoe UI', sans-serif;")
        self.setFixedSize(1000, 600)

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # --- LEFT: Image Viewer ---
        self.image_viewer = GLImageViewer()
        self.image_viewer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.left_pane = QWidget()
        self.left_layout = QVBoxLayout(self.left_pane)
        self.left_layout.setContentsMargins(0,0,0,0)
        self.left_layout.setSpacing(0)

        self.toolbar = QWidget()
        self.toolbar.setStyleSheet("background-color: #252525; border-bottom: 1px solid #333;")
        self.tb_layout = QHBoxLayout(self.toolbar)
        self.btn_open = QPushButton("Open Image")
        self.btn_open.setStyleSheet("background-color: #444; color: white; border-radius: 4px; padding: 5px 10px;")
        self.btn_open.clicked.connect(self.open_image)
        self.tb_layout.addWidget(self.btn_open)
        self.tb_layout.addStretch()

        self.left_layout.addWidget(self.toolbar)
        self.left_layout.addWidget(self.image_viewer)

        self.main_layout.addWidget(self.left_pane)

        # --- RIGHT: Control Panel ---
        self.controls_panel = QWidget()
        self.controls_panel.setFixedWidth(350)
        self.controls_panel.setStyleSheet("background-color: #1e1e1e; border-left: 1px solid #333;")

        controls_layout = QVBoxLayout(self.controls_panel)
        controls_layout.setSpacing(10)
        controls_layout.setContentsMargins(15, 15, 15, 15)

        top_bar = QHBoxLayout()
        title = QLabel("Curves")
        title.setStyleSheet("color: #ddd; font-weight: bold; font-size: 14px;")
        auto_btn = QPushButton("AUTO")
        auto_btn.setFixedSize(50, 20)
        auto_btn.setStyleSheet("QPushButton { background-color: #333; color: #aaa; border-radius: 10px; border: 1px solid #555; font-size: 10px; }")
        top_bar.addWidget(title)
        top_bar.addStretch()
        top_bar.addWidget(auto_btn)
        controls_layout.addLayout(top_bar)

        combo = StyledComboBox()
        combo.addItems(["RGB", "Red", "Green", "Blue"])
        controls_layout.addWidget(combo)

        tools_layout = QHBoxLayout()
        tools_frame = QFrame()
        tools_frame.setStyleSheet(".QFrame { background-color: #383838; border-radius: 5px; border: 1px solid #555; }")
        tf_layout = QHBoxLayout(tools_frame)
        tf_layout.setContentsMargins(0, 0, 0, 0)
        tf_layout.setSpacing(0)

        btn_black = IconButton(ICON_PATH_BLACK, "Set Black Point")
        btn_gray = IconButton(ICON_PATH_GRAY, "Set Gray Point")
        btn_white = IconButton(ICON_PATH_WHITE, "Set White Point")
        border_style = "border-right: 1px solid #555;"
        btn_black.setStyleSheet(btn_black.styleSheet() + border_style)
        btn_gray.setStyleSheet(btn_gray.styleSheet() + border_style)

        tf_layout.addWidget(btn_black)
        tf_layout.addWidget(btn_gray)
        tf_layout.addWidget(btn_white)
        tools_layout.addWidget(tools_frame)

        self.btn_add_point = IconButton(ICON_PATH_ADD, "Add Point to Curve")
        self.btn_add_point.clicked.connect(lambda: self.curve.add_point_center())
        self.btn_add_point.setFixedSize(40, 32)
        self.btn_add_point.setStyleSheet("""
            QPushButton { background-color: #383838; border: 1px solid #555; border-radius: 4px; }
            QPushButton:hover { background-color: #444; }
        """)

        tools_layout.addSpacing(5)
        tools_layout.addWidget(self.btn_add_point)
        tools_layout.addStretch()
        controls_layout.addLayout(tools_layout)

        self.curve = CurveGraph()
        self.curve.lutChanged.connect(self.image_viewer.upload_lut)
        controls_layout.addWidget(self.curve)

        grad_bar = QFrame()
        grad_bar.setFixedHeight(15)
        grad_bar.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 black, stop:1 white); border-radius: 2px;")
        controls_layout.addWidget(grad_bar)

        controls_layout.addStretch()
        self.main_layout.addWidget(self.controls_panel)

    def open_image(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if file_path:
            try:
                img = Image.open(file_path).convert("RGBA")

                # Calculate Histogram
                data = np.array(img)
                if HAS_NUMBA:
                    hist = calculate_histogram_numba(data)
                else:
                    hist = calculate_histogram_numpy(data)
                self.curve.set_histogram(hist)

                # Upload to GL
                self.image_viewer.set_image(img)

                # Initial LUT trigger
                self.curve.update_lut()
            except Exception as e:
                print(f"Error loading image: {e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    QSurfaceFormat.setDefaultFormat(fmt)

    win = CurvesDemo()
    win.show()
    sys.exit(app.exec())
