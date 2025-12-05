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
    float g = texture(curveLUT, col.g).g;
    float b = texture(curveLUT, col.b).b;

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
             # Default LUT: linear RGB
             lut = np.linspace(0, 1, 256, dtype=np.float32)
             lut = np.stack([lut, lut, lut], axis=1) # (256, 3)
             self.upload_lut(lut)

    def upload_lut(self, lut_data):
        if not self.isValid():
            self._pending_lut = lut_data
            return

        # Ensure data is contiguous and float32
        lut_data = np.ascontiguousarray(lut_data, dtype=np.float32)

        # Reshape if necessary, we expect (256, 3)
        if lut_data.ndim == 1 and len(lut_data) == 256:
             # If passed 1D array, treat as grayscale/all-channels
             lut_data = np.stack([lut_data]*3, axis=1)

        self.makeCurrent()
        if self.lut_texture is None:
            self.lut_texture = gl.glGenTextures(1)

        gl.glBindTexture(gl.GL_TEXTURE_1D, self.lut_texture)
        gl.glTexParameteri(gl.GL_TEXTURE_1D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_1D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_1D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)

        gl.glTexImage1D(gl.GL_TEXTURE_1D, 0, gl.GL_RGB, 256, 0, gl.GL_RGB, gl.GL_FLOAT, lut_data)
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
class InputLevelSliders(QWidget):
    """
    An interactive slider widget for setting black and white input level points.
    These points control the clamping range of a curve, typically used for image
    processing to adjust the input range. The widget emits signals when the black
    or white points are changed.
    """
    blackPointChanged = Signal(float)
    whitePointChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setStyleSheet("background-color: #222222;")

        self._black_val = 0.0
        self._white_val = 1.0
        self._dragging = None  # 'black', 'white', or None

        # Style constants
        self.handle_width = 12
        self.handle_height = 18
        self.hit_radius = 15

        # Magic numbers replacement
        self.limit_gap = 0.01
        self.inner_circle_radius = 3
        self.hit_padding_y = 5
        self.bezier_ctrl_y_factor = 0.4
        self.bezier_ctrl_x_factor = 0.5

    def setBlackPoint(self, val):
        self._black_val = max(0.0, min(val, self._white_val - self.limit_gap))
        self.update()

    def setWhitePoint(self, val):
        self._white_val = max(self._black_val + self.limit_gap, min(val, 1.0))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Background (Track)
        painter.fillRect(self.rect(), QColor("#222222"))

        # Draw Handles
        self._draw_handle(painter, self._black_val * w, is_black=True)
        self._draw_handle(painter, self._white_val * w, is_black=False)

    def _draw_handle(self, painter, x_pos, is_black):
        # Align flush with X-axis (Top of widget)
        y_top = 0
        y_bottom = self.handle_height
        hw = self.handle_width / 2.0

        # Improved Teardrop Shape
        # Tip at (x_pos, 0)
        # Rounded bottom: Circle at (x_pos, y_bottom - radius)

        radius = hw  # 5.0
        cy = y_bottom - radius

        path = QPainterPath()
        path.moveTo(x_pos, y_top)

        # Draw teardrop shape: use cubic Bezier curves for the sides, blending smoothly into a semicircular bottom.
        path.cubicTo(x_pos + hw * self.bezier_ctrl_x_factor, y_top + self.handle_height * self.bezier_ctrl_y_factor,
                     x_pos + hw, cy - radius * self.bezier_ctrl_x_factor,
                     x_pos + hw, cy)

        # Bottom arc
        # Arc from 0 degrees (right) to 180 degrees (left) via bottom
        # Rect for arc: (x_pos - radius, cy - radius, 2*radius, 2*radius)
        path.arcTo(x_pos - radius, cy - radius, 2*radius, 2*radius, 0, -180)

        # Left side curve (back to top)
        path.cubicTo(x_pos - hw, cy - radius * self.bezier_ctrl_x_factor,
                     x_pos - hw * self.bezier_ctrl_x_factor, y_top + self.handle_height * self.bezier_ctrl_y_factor,
                     x_pos, y_top)

        # Fill - Light Gray
        painter.setBrush(QColor("#BBBBBB"))
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        # Inner Circle
        center_y_circle = cy # Centered in the bulb part

        inner_color = QColor("black") if is_black else QColor("white")
        painter.setBrush(inner_color)
        painter.drawEllipse(QPointF(x_pos, center_y_circle), self.inner_circle_radius, self.inner_circle_radius)

    def mousePressEvent(self, event):
        pos = event.position()
        w = self.width()

        bx = self._black_val * w
        wx = self._white_val * w

        # Check distance
        dist_b = abs(pos.x() - bx)
        dist_w = abs(pos.x() - wx)

        # Check Y range: Click must be within the handle height approx
        if pos.y() <= self.handle_height + self.hit_padding_y:
            # Prioritize closer one
            if dist_b < self.hit_radius and dist_b <= dist_w:
                self._dragging = 'black'
            elif dist_w < self.hit_radius:
                self._dragging = 'white'

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return

        w = self.width()
        if w == 0: return

        val = event.position().x() / w
        val = max(0.0, min(1.0, val))

        if self._dragging == 'black':
            # Constraint: cannot cross white point (minus small gap)
            limit = self._white_val - self.limit_gap
            if val > limit: val = limit
            self._black_val = val
            self.blackPointChanged.emit(val)

        elif self._dragging == 'white':
            limit = self._black_val + self.limit_gap
            if val < limit: val = limit
            self._white_val = val
            self.whitePointChanged.emit(val)

        self.update()

    def mouseReleaseEvent(self, event):
        self._dragging = None


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
    startPointMoved = Signal(float)
    endPointMoved = Signal(float)

    HIT_DETECTION_RADIUS = 15  # pixels
    MIN_DISTANCE_THRESHOLD = 0.01

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self.setStyleSheet("background-color: #2b2b2b;")

        # 2.1 Independent Data Models
        self.active_channel = "RGB"
        self.channels = {
            "RGB": [QPointF(0.0, 0.0), QPointF(1.0, 1.0)],
            "Red": [QPointF(0.0, 0.0), QPointF(1.0, 1.0)],
            "Green": [QPointF(0.0, 0.0), QPointF(1.0, 1.0)],
            "Blue": [QPointF(0.0, 0.0), QPointF(1.0, 1.0)]
        }
        self.splines = {}

        self.selected_index = -1
        self.dragging = False
        self.histogram_data = None

        # Initial calculation
        self._recalculate_splines_all()

    def set_channel(self, channel_name):
        if channel_name in self.channels and channel_name != self.active_channel:
            self.active_channel = channel_name
            self.selected_index = -1
            # Emit signals to sync sliders with new channel's endpoints
            points = self.channels[self.active_channel]
            if points:
                self.startPointMoved.emit(points[0].x())
                self.endPointMoved.emit(points[-1].x())
            self.update()

    def set_histogram(self, hist_data):
        self.histogram_data = hist_data
        self.update()

    def _recalculate_splines_all(self):
        for name in self.channels:
            self._recalculate_spline(name)

    def _recalculate_spline(self, channel_name):
        points = self.channels[channel_name]
        points.sort(key=lambda p: p.x())
        x = [p.x() for p in points]
        y = [p.y() for p in points]
        self.splines[channel_name] = MonotoneCubicSpline(x, y)

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
        current_points = self.channels[self.active_channel]

        # Determine point color based on channel
        if self.active_channel == "Red":
            pt_color = QColor("#FF4444")
        elif self.active_channel == "Green":
            pt_color = QColor("#44FF44")
        elif self.active_channel == "Blue":
            pt_color = QColor("#4444FF")
        else:
            pt_color = QColor("white")

        for i, p in enumerate(current_points):
            sx = p.x() * w
            sy = h - (p.y() * h)

            # Visual Feedback: Hollow for selected
            if i == self.selected_index:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(pt_color, 2))
                painter.drawEllipse(QPointF(sx, sy), point_radius + 2, point_radius + 2)

            painter.setBrush(pt_color)
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.drawEllipse(QPointF(sx, sy), point_radius, point_radius)

    def draw_fake_histogram(self, painter, w, h):
        # Fallback noise for design time
        if self.active_channel != "RGB": return # Simplify fake view for individual channels?

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
        # 2.2 Dynamic Histogram Visualization
        # Assuming histogram_data is (3, 256) float array normalized to 0..1
        if self.histogram_data is None: return

        # If shape is 1D, it's grayscale.
        is_gray = (len(self.histogram_data.shape) == 1)

        if is_gray:
            self._draw_hist_channel(painter, self.histogram_data, QColor(120, 120, 120, 128), w, h)
            return

        if self.active_channel == "RGB":
            # Draw all combined
            self._draw_hist_channel(painter, self.histogram_data[0], QColor(255, 0, 0, 100), w, h)
            self._draw_hist_channel(painter, self.histogram_data[1], QColor(0, 255, 0, 100), w, h)
            self._draw_hist_channel(painter, self.histogram_data[2], QColor(0, 0, 255, 100), w, h)
        elif self.active_channel == "Red":
            self._draw_hist_channel(painter, self.histogram_data[0], QColor(255, 0, 0, 150), w, h)
        elif self.active_channel == "Green":
            self._draw_hist_channel(painter, self.histogram_data[1], QColor(0, 255, 0, 150), w, h)
        elif self.active_channel == "Blue":
            self._draw_hist_channel(painter, self.histogram_data[2], QColor(0, 0, 255, 150), w, h)

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
        spline = self.splines.get(self.active_channel)
        if spline is None:
            return

        points = self.channels[self.active_channel]
        start_pt = points[0]
        end_pt = points[-1]

        # 2.3 Visual Feedback (Curve Color)
        color_map = {
            "RGB": QColor("#FFFFFF"),
            "Red": QColor("#FF4444"),
            "Green": QColor("#44FF44"),
            "Blue": QColor("#4444FF")
        }
        pen_color = color_map.get(self.active_channel, QColor("white"))

        steps = w // 2
        if steps < 100: steps = 100

        xs = np.linspace(0, 1, steps)
        ys = spline.evaluate(xs)

        # Apply Clamping for visualization
        # Clamping is explicitly applied in the loop below: for x < start_pt.x(), y is set to start_pt.y();
        # for x > end_pt.x(), y is set to end_pt.y(). This overrides any extrapolation or edge-value behavior
        # from spline.evaluate, ensuring the flat lines are drawn correctly for the "clamped" regions.
        # We assume xs covers 0 to 1 sufficiently to show the flat clamped regions.

        path = QPainterPath()

        first_pt = True

        for i in range(len(xs)):
            x_val = xs[i]

            if x_val < start_pt.x():
                y_val = start_pt.y()
            elif x_val > end_pt.x():
                y_val = end_pt.y()
            else:
                y_val = ys[i]

            cx = x_val * w
            cy = h - y_val * h

            if first_pt:
                path.moveTo(cx, cy)
                first_pt = False
            else:
                path.lineTo(cx, cy)

        painter.setPen(QPen(pen_color, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def mousePressEvent(self, event):
        pos = event.position()
        w, h = self.width(), self.height()

        # Operates on active channel points
        points = self.channels[self.active_channel]

        hit_radius_sq = self.HIT_DETECTION_RADIUS ** 2
        click_idx = -1
        min_dist_sq = float('inf')

        for i, p in enumerate(points):
            sx, sy = p.x() * w, h - p.y() * h
            dist_sq = (pos.x() - sx) ** 2 + (pos.y() - sy) ** 2
            if dist_sq < hit_radius_sq:
                if dist_sq < min_dist_sq:
                    min_dist_sq = dist_sq
                    click_idx = i

        if click_idx != -1:
            self.selected_index = click_idx
            if event.button() == Qt.RightButton and 0 < click_idx < len(points) - 1:
                points.pop(click_idx)
                self.selected_index = -1
                self.update_spline_and_lut()
        else:
            # Add point logic
            nx = max(0.0, min(1.0, pos.x() / w))
            insert_i = len(points)
            for i, p in enumerate(points):
                if p.x() > nx:
                    insert_i = i
                    break

            ny = max(0.0, min(1.0, (h - pos.y()) / h))

            prev_x = points[insert_i-1].x() if insert_i > 0 else 0
            next_x = points[insert_i].x() if insert_i < len(points) else 1

            if nx > prev_x + self.MIN_DISTANCE_THRESHOLD and nx < next_x - self.MIN_DISTANCE_THRESHOLD:
                points.insert(insert_i, QPointF(nx, ny))
                self.selected_index = insert_i
                self.update_spline_and_lut()

        self.dragging = True

    def mouseMoveEvent(self, event):
        if self.dragging and self.selected_index != -1:
            points = self.channels[self.active_channel]
            pos = event.position()
            w, h = self.width(), self.height()

            nx = max(0.0, min(1.0, pos.x() / w))
            ny = max(0.0, min(1.0, (h - pos.y()) / h))

            if self.selected_index == 0:
                # Start Point - moveable X
                # Constraint: cannot cross next point
                if len(points) > 1:
                    max_x = points[1].x() - self.MIN_DISTANCE_THRESHOLD
                    if nx > max_x: nx = max_x
                nx = max(0.0, nx) # Cannot go below 0

            elif self.selected_index == len(points) - 1:
                # End Point - moveable X
                # Constraint: cannot cross prev point
                if len(points) > 1:
                    min_x = points[self.selected_index - 1].x() + self.MIN_DISTANCE_THRESHOLD
                    if nx < min_x: nx = min_x
                nx = min(1.0, nx) # Cannot go above 1

            else:
                prev_p = points[self.selected_index - 1]
                next_p = points[self.selected_index + 1]
                min_x = prev_p.x() + self.MIN_DISTANCE_THRESHOLD
                max_x = next_p.x() - self.MIN_DISTANCE_THRESHOLD

                if nx < min_x: nx = min_x
                if nx > max_x: nx = max_x

            points[self.selected_index] = QPointF(nx, ny)
            self.update_spline_and_lut()

            # Emit signals if endpoints moved
            if self.selected_index == 0:
                self.startPointMoved.emit(nx)
            elif self.selected_index == len(points) - 1:
                self.endPointMoved.emit(nx)

    def mouseReleaseEvent(self, event):
        self.dragging = False

    def add_point_smart(self):
        """
        2.4 "Add Point" Button Logic
        """
        points = self.channels[self.active_channel]
        if len(points) < 2: return

        max_gap = -1.0
        max_gap_idx = -1

        for i in range(len(points) - 1):
            gap = points[i+1].x() - points[i].x()
            if gap > max_gap:
                max_gap = gap
                max_gap_idx = i

        if max_gap_idx != -1:
            p0 = points[max_gap_idx]
            p1 = points[max_gap_idx + 1]
            mid_x = p0.x() + (p1.x() - p0.x()) * 0.5

            spline = self.splines.get(self.active_channel)
            if spline:
                mid_y = spline.evaluate(mid_x)
            else:
                mid_y = p0.y() + (p1.y() - p0.y()) * 0.5
            mid_y = max(0.0, min(1.0, mid_y))

            new_point = QPointF(mid_x, mid_y)
            insert_at = max_gap_idx + 1
            points.insert(insert_at, new_point)

            self.selected_index = insert_at
            self.update_spline_and_lut()

    def add_point_center(self):
        self.add_point_smart()

    def update_spline_and_lut(self):
        self._recalculate_spline(self.active_channel)
        self.update_lut()
        self.update()

    def update_lut(self):
        # 3.1 Composite Logic: Output = Master(Channel(Input))

        # Base input: 0..1
        xs = np.linspace(0, 1, 256)

        # Helper to get eval from spline or identity if missing
        # Also applies clamping logic for points outside [start_x, end_x]
        def eval_spline(name, inputs):
            s = self.splines.get(name)
            if s:
                # Determine start/end x/y from points
                pts = self.channels[name]
                start_pt = pts[0]
                end_pt = pts[-1]

                # Evaluate spline
                vals = s.evaluate(inputs).copy()

                # Clamp based on X range of the curve definition
                # inputs < start_pt.x => start_pt.y
                # inputs > end_pt.x => end_pt.y

                # Masks
                mask_low = inputs < start_pt.x()
                mask_high = inputs > end_pt.x()

                vals[mask_low] = start_pt.y()
                vals[mask_high] = end_pt.y()

                return np.clip(vals, 0.0, 1.0)
            return inputs # Identity

        # 1. Apply individual channel curves
        r_curve = eval_spline("Red", xs)
        g_curve = eval_spline("Green", xs)
        b_curve = eval_spline("Blue", xs)

        # 2. Apply master curve to the result of individual curves
        # We reuse eval_spline but pass the intermediate results as inputs
        r_final = eval_spline("RGB", r_curve)
        g_final = eval_spline("RGB", g_curve)
        b_final = eval_spline("RGB", b_curve)

        # Stack: (256, 3)
        lut = np.stack([r_final, g_final, b_final], axis=1).astype(np.float32)

        self.lutChanged.emit(lut)


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
        combo.currentTextChanged.connect(lambda text: self.curve.set_channel(text))
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

        # -- Graph + Sliders Group (Spacing=0) --
        graph_sliders_layout = QVBoxLayout()
        graph_sliders_layout.setSpacing(0)
        graph_sliders_layout.setContentsMargins(0, 0, 0, 0)

        self.curve = CurveGraph()
        self.curve.lutChanged.connect(self.image_viewer.upload_lut)
        graph_sliders_layout.addWidget(self.curve)

        # 2.2 Replace Gradient Bar with Interactive Sliders
        self.input_sliders = InputLevelSliders()

        # Connect Sliders <-> Graph
        # Sliders -> Graph
        self.input_sliders.blackPointChanged.connect(self.update_black_point)
        self.input_sliders.whitePointChanged.connect(self.update_white_point)

        # Graph -> Sliders
        self.curve.startPointMoved.connect(self.input_sliders.setBlackPoint)
        self.curve.endPointMoved.connect(self.input_sliders.setWhitePoint)

        graph_sliders_layout.addWidget(self.input_sliders)

        controls_layout.addLayout(graph_sliders_layout)

        controls_layout.addStretch()
        self.main_layout.addWidget(self.controls_panel)

    def update_black_point(self, val):
        # User moved the black slider. We need to update the Curve's start point (index 0).
        points = self.curve.channels[self.curve.active_channel]
        if not points: return

        # Update point
        p0 = points[0]
        points[0] = QPointF(val, p0.y())

        # Trigger update in curve
        self.curve.update_spline_and_lut()

    def update_white_point(self, val):
        # User moved the white slider. We need to update the Curve's end point (index -1).
        points = self.curve.channels[self.curve.active_channel]
        if not points: return

        # Update point
        p_end = points[-1]
        points[-1] = QPointF(val, p_end.y())

        # Trigger update in curve
        self.curve.update_spline_and_lut()

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
