import sys
import os
import numpy as np
from PIL import Image

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QFrame, QSizePolicy, QFileDialog
)
from PySide6.QtCore import Qt, QPointF, Signal, QRectF
from PySide6.QtGui import (
    QPainter, QColor, QPen, QPainterPath, QSurfaceFormat, QPolygonF
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL import GL as gl


# ==========================================
# Utils
# ==========================================
def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def ease_shift(base: float, delta: float, bound: float, exponent: float = 2.0) -> float:
    """Nonlinear shift: full speed near base, decelerating toward bound.

    *base*  – starting position of the follower handle.
    *delta* – signed displacement requested (same sign convention as the
              midtone delta).
    *bound* – the hard limit the follower must never exceed (0.0 for shadow,
              1.0 for highlight).
    *exponent* – controls how aggressively the handle slows down near
                 *bound*.  Higher = more damping near the edge.

    Returns the new position for the follower handle, always between
    *base* and *bound* (inclusive).
    """
    span = abs(bound - base)
    if span < 1e-9:
        return base
    ratio = clamp01(abs(delta) / span)
    effective = span * (1.0 - pow(1.0 - ratio, exponent))
    if delta >= 0:
        return base + effective if bound >= base else base - effective
    else:
        return base - effective if bound <= base else base + effective


def build_levels_lut(handles):
    """
    ✅ 新算法：5 个固定输出锚点(0, .25, .5, .75, 1)，手柄控制输入 x 位置
    曲线经过：
      (x0, 0.00)
      (x1, 0.25)
      (x2, 0.50)
      (x3, 0.75)
      (x4, 1.00)

    当 handles = [0, .25, .5, .75, 1] => y=x（恒等，无任何更改）
    返回 (256,3) float32 LUT in [0,1]
    """
    if len(handles) != 5:
        raise ValueError("handles must be length 5")

    xs = [clamp01(float(v)) for v in handles]
    # 强制非递减（你的拖拽约束已经保证了，这里再兜底）
    for i in range(1, 5):
        if xs[i] < xs[i - 1]:
            xs[i] = xs[i - 1]

    ys = [0.0, 0.25, 0.50, 0.75, 1.0]

    # 生成 LUT：对每个输入 t，找所在 segment 做线性插值
    t = np.linspace(0.0, 1.0, 256, dtype=np.float32)
    out = np.empty_like(t)

    # 分段处理，避免在 Python 循环里做 256 次查找也可以，但这里足够快且清晰
    # 先处理 t <= x0 和 t >= x4
    x0, x1, x2, x3, x4 = xs
    y0, y1, y2, y3, y4 = ys

    out[t <= x0] = y0
    out[t >= x4] = y4

    def interp_segment(mask, xa, xb, ya, yb):
        # xa == xb 时形成“竖直段”（阶跃），这里用 yb（也可用 ya，看你更喜欢哪种手感）
        denom = (xb - xa)
        if denom <= 1e-8:
            out[mask] = yb
        else:
            u = (t[mask] - xa) / denom
            out[mask] = ya + u * (yb - ya)

    # (x0,x1)->(y0,y1)
    m01 = (t > x0) & (t < x1)
    interp_segment(m01, x0, x1, y0, y1)

    # (x1,x2)->(y1,y2)
    m12 = (t >= x1) & (t < x2)
    interp_segment(m12, x1, x2, y1, y2)

    # (x2,x3)->(y2,y3)
    m23 = (t >= x2) & (t < x3)
    interp_segment(m23, x2, x3, y2, y3)

    # (x3,x4)->(y3,y4)
    m34 = (t >= x3) & (t < x4)
    interp_segment(m34, x3, x4, y3, y4)

    out = np.clip(out, 0.0, 1.0).astype(np.float32)
    lut = np.stack([out, out, out], axis=1).astype(np.float32)
    return lut


# ==========================================
# StyledComboBox
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
            QComboBox::drop-down { border: 0px; width: 25px; }
            QComboBox::down-arrow { image: none; border: none; }
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


# ==========================================
# OpenGL Viewer with 1D LUT
# ==========================================
VERTEX_SHADER = """#version 330 core
layout (location = 0) in vec2 aPos;
layout (location = 1) in vec2 aTex;

out vec2 vTex;

void main(){
    gl_Position = vec4(aPos, 0.0, 1.0);
    vTex = vec2(aTex.x, 1.0 - aTex.y);
}
"""

FRAGMENT_SHADER = """#version 330 core
out vec4 FragColor;
in vec2 vTex;

uniform sampler2D uImage;
uniform sampler1D uLUT;
uniform bool uHasImage;

void main(){
    if(!uHasImage){
        FragColor = vec4(0.15,0.15,0.15,1.0);
        return;
    }
    vec4 c = texture(uImage, vTex);
    float r = texture(uLUT, c.r).r;
    float g = texture(uLUT, c.g).g;
    float b = texture(uLUT, c.b).b;
    FragColor = vec4(r,g,b,c.a);
}
"""


class GLLevelsViewer(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.shader = None
        self.vao = None
        self.vbo = None

        self.image_tex = None
        self.lut_tex = None
        self.has_image = False

        self._pending_lut = None
        self._pending_image = None  # PIL image

    def initializeGL(self):
        gl.glClearColor(0.15, 0.15, 0.15, 1.0)

        self.shader = self._create_program(VERTEX_SHADER, FRAGMENT_SHADER)

        vertices = np.array([
            -1.0, -1.0,  0.0, 0.0,
             1.0, -1.0,  1.0, 0.0,
             1.0,  1.0,  1.0, 1.0,
            -1.0,  1.0,  0.0, 1.0,
        ], dtype=np.float32)

        self.vao = gl.glGenVertexArrays(1)
        self.vbo = gl.glGenBuffers(1)

        gl.glBindVertexArray(self.vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self.vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, vertices.nbytes, vertices, gl.GL_STATIC_DRAW)

        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(0, 2, gl.GL_FLOAT, gl.GL_FALSE, 4 * 4, None)

        gl.glEnableVertexAttribArray(1)
        gl.glVertexAttribPointer(1, 2, gl.GL_FLOAT, gl.GL_FALSE, 4 * 4, gl.ctypes.c_void_p(2 * 4))

        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
        gl.glBindVertexArray(0)

        # init LUT to identity with default handles (0, .25, .5, .75, 1)
        identity_handles = [0.0, 0.25, 0.50, 0.75, 1.0]
        self.upload_lut(build_levels_lut(identity_handles))

        if self._pending_image is not None:
            self.set_image(self._pending_image)
            self._pending_image = None

        if self._pending_lut is not None:
            self.upload_lut(self._pending_lut)
            self._pending_lut = None

    def _create_program(self, vs_src, fs_src):
        def compile_shader(src, stype):
            sid = gl.glCreateShader(stype)
            gl.glShaderSource(sid, src)
            gl.glCompileShader(sid)
            ok = gl.glGetShaderiv(sid, gl.GL_COMPILE_STATUS)
            if not ok:
                log = gl.glGetShaderInfoLog(sid).decode("utf-8", errors="ignore")
                raise RuntimeError(log)
            return sid

        vs = compile_shader(vs_src, gl.GL_VERTEX_SHADER)
        fs = compile_shader(fs_src, gl.GL_FRAGMENT_SHADER)
        pid = gl.glCreateProgram()
        gl.glAttachShader(pid, vs)
        gl.glAttachShader(pid, fs)
        gl.glLinkProgram(pid)
        ok = gl.glGetProgramiv(pid, gl.GL_LINK_STATUS)
        if not ok:
            log = gl.glGetProgramInfoLog(pid).decode("utf-8", errors="ignore")
            raise RuntimeError(log)
        gl.glDeleteShader(vs)
        gl.glDeleteShader(fs)
        return pid

    def set_image(self, img_pil: Image.Image):
        if not self.isValid():
            self._pending_image = img_pil
            return

        self.makeCurrent()
        img = img_pil.convert("RGBA")
        data = np.array(img)
        w, h = img.size

        if self.image_tex is None:
            self.image_tex = gl.glGenTextures(1)

        gl.glBindTexture(gl.GL_TEXTURE_2D, self.image_tex)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexImage2D(
            gl.GL_TEXTURE_2D, 0, gl.GL_RGBA, w, h, 0,
            gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, data
        )
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        self.has_image = True
        self.doneCurrent()
        self.update()

    def upload_lut(self, lut_data: np.ndarray):
        if not self.isValid():
            self._pending_lut = lut_data
            return

        lut = np.ascontiguousarray(lut_data.astype(np.float32))
        if lut.ndim == 1:
            lut = np.stack([lut, lut, lut], axis=1)
        if lut.shape != (256, 3):
            raise ValueError("LUT must be (256,3)")

        self.makeCurrent()
        if self.lut_tex is None:
            self.lut_tex = gl.glGenTextures(1)

        gl.glBindTexture(gl.GL_TEXTURE_1D, self.lut_tex)
        gl.glTexParameteri(gl.GL_TEXTURE_1D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_1D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_1D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexImage1D(
            gl.GL_TEXTURE_1D, 0, gl.GL_RGB, 256, 0,
            gl.GL_RGB, gl.GL_FLOAT, lut
        )
        gl.glBindTexture(gl.GL_TEXTURE_1D, 0)
        self.doneCurrent()
        self.update()

    def paintGL(self):
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        if self.shader is None:
            return

        gl.glUseProgram(self.shader)
        gl.glUniform1i(gl.glGetUniformLocation(self.shader, "uHasImage"), 1 if self.has_image else 0)

        if self.has_image and self.image_tex is not None:
            gl.glActiveTexture(gl.GL_TEXTURE0)
            gl.glBindTexture(gl.GL_TEXTURE_2D, self.image_tex)
            gl.glUniform1i(gl.glGetUniformLocation(self.shader, "uImage"), 0)

        if self.lut_tex is not None:
            gl.glActiveTexture(gl.GL_TEXTURE1)
            gl.glBindTexture(gl.GL_TEXTURE_1D, self.lut_tex)
            gl.glUniform1i(gl.glGetUniformLocation(self.shader, "uLUT"), 1)

        gl.glBindVertexArray(self.vao)
        gl.glDrawArrays(gl.GL_TRIANGLE_FAN, 0, 4)
        gl.glBindVertexArray(0)
        gl.glUseProgram(0)


# ==========================================
# LevelsComposite UI
# ==========================================
class LevelsComposite(QWidget):
    valuesChanged = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("background-color: #2b2b2b; border-radius: 6px;")

        self.histogram_data = None
        self.active_channel = "RGB"

        # ✅ 保持初始位置 0/25/50/75/100
        self.handles = [0.0, 0.25, 0.50, 0.75, 1.0]

        self.hover_index = -1
        self.drag_index = -1
        self._drag_start_handles = None

        self.margin_side = 8
        self.hist_height = 120
        self.base_handle_width = 12

    def set_histogram(self, hist_data):
        self.histogram_data = hist_data
        self.update()

    def set_channel(self, channel):
        self.active_channel = channel
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()

        painter.fillRect(self.rect(), QColor("#2b2b2b"))

        axis_y = self.hist_height
        track_left = self.margin_side
        track_width = w - 2 * self.margin_side

        painter.save()
        hist_rect = QRectF(self.margin_side, 0, track_width, axis_y)
        painter.setClipRect(hist_rect)
        if self.histogram_data is not None:
            self.draw_real_histogram(painter, hist_rect)
        else:
            self.draw_fake_histogram(painter, hist_rect)
        painter.restore()

        self.draw_smart_guides(painter, w, axis_y)

        painter.setPen(QPen(QColor(80, 80, 80), 1))
        painter.drawLine(QPointF(track_left, axis_y), QPointF(w - self.margin_side, axis_y))

        self.draw_handles(painter, w, axis_y)

    def draw_smart_guides(self, painter, w, axis_y):
        track_width = w - 2 * self.margin_side
        line_color = QColor(255, 255, 255, 60)
        triangle_color = QColor(100, 100, 100)
        anchor_map = {1: 0.25, 2: 0.50, 3: 0.75}

        for i, handle_val in enumerate(self.handles):
            handle_x = self.margin_side + handle_val * track_width

            if i in anchor_map:
                anchor_x = self.margin_side + anchor_map[i] * track_width
                painter.setPen(QPen(line_color, 1))
                painter.drawLine(QPointF(anchor_x, 0), QPointF(handle_x, axis_y))
                self._draw_inverted_triangle(painter, anchor_x, 0, 6, triangle_color)
            elif i in [0, 4]:
                painter.setPen(QPen(line_color, 1, Qt.DashLine))
                painter.drawLine(QPointF(handle_x, 0), QPointF(handle_x, axis_y))

    def draw_handles(self, painter, w, axis_y):
        track_width = w - 2 * self.margin_side

        for i, val in enumerate(self.handles):
            cx = self.margin_side + val * track_width
            is_active = (i == self.drag_index) or (i == self.hover_index)

            scale = 1.0
            draw_dot = False
            fill_color = QColor("#888888")
            dot_color = None

            if i == 0:
                scale = 1.0; draw_dot = True; fill_color = QColor("#666666"); dot_color = QColor("#000000")
            elif i == 1:
                scale = 0.75; fill_color = QColor("#444444")
            elif i == 2:
                scale = 1.0; fill_color = QColor("#888888")
            elif i == 3:
                scale = 0.75; fill_color = QColor("#BBBBBB")
            elif i == 4:
                scale = 1.0; draw_dot = True; fill_color = QColor("#AAAAAA"); dot_color = QColor("#FFFFFF")

            self._draw_teardrop(painter, cx, axis_y, scale, fill_color, draw_dot, dot_color, is_active)

    def _draw_teardrop(self, painter, x_pos, y_top, scale, fill_color, draw_dot, dot_color, is_active):
        w = self.base_handle_width * scale
        h = 18 * scale
        hw = w / 2.0
        bezier_ctrl_y = h * 0.4
        radius = hw
        cy_circle = y_top + h - radius

        path = QPainterPath()
        path.moveTo(x_pos, y_top)
        path.cubicTo(x_pos + hw * 0.5, y_top + bezier_ctrl_y,
                     x_pos + hw, cy_circle - radius * 0.5,
                     x_pos + hw, cy_circle)
        path.arcTo(x_pos - radius, cy_circle - radius, 2 * radius, 2 * radius, 0, -180)
        path.cubicTo(x_pos - hw, cy_circle - radius * 0.5,
                     x_pos - hw * 0.5, y_top + bezier_ctrl_y,
                     x_pos, y_top)

        painter.setBrush(fill_color)
        if is_active:
            painter.setPen(QPen(QColor("#4a90e2"), 1.5))
        else:
            painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        if draw_dot and dot_color:
            painter.setBrush(dot_color)
            painter.setPen(Qt.NoPen)
            dot_r = 2.5 * scale
            painter.drawEllipse(QPointF(x_pos, cy_circle), dot_r, dot_r)

    def _draw_inverted_triangle(self, painter, x, y, size, color):
        half = size / 2
        polygon = QPolygonF([QPointF(x - half, y), QPointF(x + half, y), QPointF(x, y + size)])
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(polygon)

    def draw_fake_histogram(self, painter, rect):
        x_start = rect.left()
        width = rect.width()
        height = rect.height()
        bottom = rect.bottom()

        path = QPainterPath()
        path.moveTo(x_start, bottom)

        import math
        steps = 100
        for i in range(steps + 1):
            tt = i / steps
            x = x_start + tt * width
            v1 = 0.6 * math.exp(-((tt - 0.25) ** 2) / 0.03)
            v2 = 0.5 * math.exp(-((tt - 0.55) ** 2) / 0.05)
            v3 = 0.8 * math.exp(-((tt - 0.85) ** 2) / 0.015)
            y_norm = min(1.0, max(0.0, (v1 + v2 + v3)))
            y = bottom - (y_norm * height * 0.9)
            path.lineTo(x, y)

        path.lineTo(x_start + width, bottom)
        path.closeSubpath()

        fill_color = QColor(160, 160, 160, 80)
        if self.active_channel == "Red":
            fill_color = QColor(255, 50, 50, 100)
        elif self.active_channel == "Green":
            fill_color = QColor(50, 255, 50, 100)
        elif self.active_channel == "Blue":
            fill_color = QColor(50, 50, 255, 100)

        painter.setBrush(fill_color)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)
        painter.setPen(QPen(fill_color.lighter(150), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def draw_real_histogram(self, painter, rect):
        if self.histogram_data is None:
            return

        c_idx = -1
        if self.active_channel == "Red":
            c_idx = 0
        elif self.active_channel == "Green":
            c_idx = 1
        elif self.active_channel == "Blue":
            c_idx = 2

        x_start = rect.left()
        width = rect.width()
        height = rect.height()
        bottom = rect.bottom()
        bin_w = width / 256.0

        channels_to_draw = []
        if c_idx != -1:
            col = QColor(255 if c_idx == 0 else 0, 255 if c_idx == 1 else 0, 255 if c_idx == 2 else 0, 150)
            channels_to_draw.append((c_idx, col))
        else:
            channels_to_draw.append((0, QColor(255, 0, 0, 80)))
            channels_to_draw.append((1, QColor(0, 255, 0, 80)))
            channels_to_draw.append((2, QColor(0, 0, 255, 80)))

        for ch_idx, color in channels_to_draw:
            path = QPainterPath()
            path.moveTo(x_start, bottom)
            data = self.histogram_data[ch_idx]
            for i, val in enumerate(data):
                x = x_start + i * bin_w
                y = bottom - (val * height * 0.95)
                if i == 0:
                    path.lineTo(x, y)
                path.lineTo(x + bin_w, y)
            path.lineTo(x_start + width, bottom)
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawPath(path)

    def mousePressEvent(self, event):
        pos = event.position()
        if pos.y() < self.hist_height - 10:
            return

        w = self.width()
        track_width = w - 2 * self.margin_side

        closest_dist = float('inf')
        clicked_idx = -1
        for i, val in enumerate(self.handles):
            cx = self.margin_side + val * track_width
            dist = abs(pos.x() - cx)
            if dist < self.base_handle_width * 1.5 and dist < closest_dist:
                closest_dist = dist
                clicked_idx = i

        if clicked_idx != -1:
            self.drag_index = clicked_idx
            self._drag_start_handles = list(self.handles)
            self.update()

    def mouseMoveEvent(self, event):
        pos = event.position()
        w = self.width()
        track_width = w - 2 * self.margin_side
        if track_width <= 0:
            return

        val = clamp01((pos.x() - self.margin_side) / track_width)

        if self.drag_index != -1:
            idx = self.drag_index

            if idx == 2 and self._drag_start_handles is not None:
                # --- Midtone handle: nonlinear coupled dragging ---
                s = self._drag_start_handles
                # Clamp midtone between black-point and white-point handles
                min_val = self.handles[0]
                max_val = self.handles[4]
                val = max(min_val, min(max_val, val))
                self.handles[2] = val

                delta = val - s[2]  # signed displacement of midtone

                # Shadow (index 1): follows midtone, decelerates toward
                # black-point (handle 0)
                if delta < 0:
                    new1 = ease_shift(s[1], delta, s[0], exponent=2.0)
                else:
                    new1 = ease_shift(s[1], delta, s[4], exponent=2.0)
                new1 = clamp01(new1)
                new1 = max(self.handles[0], min(new1, self.handles[2]))
                self.handles[1] = new1

                # Highlight (index 3): follows midtone, decelerates toward
                # white-point (handle 4)
                if delta > 0:
                    new3 = ease_shift(s[3], delta, s[4], exponent=2.0)
                else:
                    new3 = ease_shift(s[3], delta, s[0], exponent=2.0)
                new3 = clamp01(new3)
                new3 = max(self.handles[2], min(new3, self.handles[4]))
                self.handles[3] = new3
            else:
                # --- Other handles: original non-decreasing constraint ---
                min_val = self.handles[idx - 1] if idx > 0 else 0.0
                max_val = self.handles[idx + 1] if idx < 4 else 1.0
                val = max(min_val, min(max_val, val))
                self.handles[idx] = val

            self.valuesChanged.emit(self.handles)
            self.update()
        else:
            hover_idx = -1
            if pos.y() >= self.hist_height - 10:
                for i, h_val in enumerate(self.handles):
                    cx = self.margin_side + h_val * track_width
                    if abs(pos.x() - cx) < self.base_handle_width:
                        hover_idx = i
                        break
            if hover_idx != self.hover_index:
                self.hover_index = hover_idx
                self.update()

    def mouseReleaseEvent(self, event):
        self.drag_index = -1
        self._drag_start_handles = None
        self.update()


# ==========================================
# Control Panel
# ==========================================
class LevelsControlPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)
        self.setStyleSheet("background-color: #1e1e1e; border-left: 1px solid #333; color: #ddd;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        top_bar = QHBoxLayout()
        icon_lbl = QLabel("📊")
        icon_lbl.setStyleSheet("font-size: 16px; margin-right: 5px;")
        title = QLabel("Levels")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")

        auto_btn = QPushButton("AUTO")
        auto_btn.setFixedSize(40, 20)
        auto_btn.setStyleSheet(
            "QPushButton { background-color: #333; color: #aaa; border-radius: 10px; "
            "border: 1px solid #555; font-size: 9px; } "
            "QPushButton:hover { background-color: #444; color: white; }"
        )
        reset_icon = QPushButton("↺")
        reset_icon.setFixedSize(20, 20)
        reset_icon.setStyleSheet("border: none; color: #4a90e2; font-weight: bold;")

        top_bar.addWidget(icon_lbl)
        top_bar.addWidget(title)
        top_bar.addStretch()
        top_bar.addWidget(reset_icon)
        top_bar.addWidget(auto_btn)
        layout.addLayout(top_bar)

        self.channel_combo = StyledComboBox()
        self.channel_combo.addItems(["RGB", "Red", "Green", "Blue"])
        layout.addWidget(self.channel_combo)

        levels_frame = QFrame()
        levels_frame.setStyleSheet("QFrame { background-color: transparent; border: none; }")
        lf_layout = QVBoxLayout(levels_frame)
        lf_layout.setContentsMargins(0, 0, 0, 0)
        lf_layout.setSpacing(0)

        self.levels_comp = LevelsComposite()
        self.channel_combo.currentTextChanged.connect(self.levels_comp.set_channel)

        lf_layout.addWidget(self.levels_comp)
        layout.addWidget(levels_frame)
        layout.addStretch()

        reset_icon.clicked.connect(self.reset_levels)
        auto_btn.clicked.connect(self.reset_levels)

    def reset_levels(self):
        # ✅ 回到 0/25/50/75/100，同时算法保证恒等
        self.levels_comp.handles = [0.0, 0.25, 0.50, 0.75, 1.0]
        self.levels_comp.valuesChanged.emit(self.levels_comp.handles)
        self.levels_comp.update()

    def set_histogram_data(self, hist):
        self.levels_comp.set_histogram(hist)


# ==========================================
# Main Demo
# ==========================================
class LevelsDemo(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Levels UI Demo (Default 0/25/50/75/100 = Identity)")
        self.setStyleSheet("background-color: #1e1e1e; font-family: 'Segoe UI', sans-serif;")
        self.resize(1000, 600)

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        left_container = QWidget()
        l_layout = QVBoxLayout(left_container)
        l_layout.setContentsMargins(0, 0, 0, 0)
        l_layout.setSpacing(0)

        toolbar = QWidget()
        toolbar.setStyleSheet("background-color: #252525; border-bottom: 1px solid #333;")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(10, 6, 10, 6)

        btn_open = QPushButton("Open Image")
        btn_open.setStyleSheet("background-color: #444; color: white; padding: 5px 15px; border-radius: 4px;")
        btn_open.clicked.connect(self.open_image)
        tb_layout.addWidget(btn_open)
        tb_layout.addStretch()

        self.viewer = GLLevelsViewer()
        l_layout.addWidget(toolbar)
        l_layout.addWidget(self.viewer)

        self.controls = LevelsControlPanel()

        # UI -> LUT -> Viewer
        self.controls.levels_comp.valuesChanged.connect(self.on_levels_changed)

        # init to identity (0/25/50/75/100)
        self.on_levels_changed(self.controls.levels_comp.handles)

        self.main_layout.addWidget(left_container)
        self.main_layout.addWidget(self.controls)

    def on_levels_changed(self, handles):
        lut = build_levels_lut(handles)
        self.viewer.upload_lut(lut)

    def open_image(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not file_path:
            return
        try:
            img = Image.open(file_path)
            self.viewer.set_image(img)

            arr = np.array(img.convert("RGB"))
            hist = np.zeros((3, 256), dtype=np.float32)
            for i in range(3):
                h_data, _ = np.histogram(arr[:, :, i], bins=256, range=(0, 256))
                mx = h_data.max()
                hist[i] = (h_data / mx) if mx > 0 else h_data

            self.controls.set_histogram_data(hist)

        except Exception as e:
            print("Open image error:", e)


if __name__ == "__main__":
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv)
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    win = LevelsDemo()
    win.show()
    sys.exit(app.exec())
