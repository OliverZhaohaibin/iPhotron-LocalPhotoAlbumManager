# -*- coding: utf-8 -*-
"""
Minimal Vignette Demo using PySide6 + OpenGL 3.3 Core

Features:
- Strength slider: 0~1
- Radius slider: 0~1
- Softness slider UI: 0~1
- Softness actual shader value: maps to 0.1~1.0

Run:
    pip install PySide6 PyOpenGL numpy
    python vignette_demo.py
"""

import sys
import numpy as np

from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import (
    QImage, QSurfaceFormat, QPainter, QColor, QPen, QFont, QPainterPath
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import (
    QOpenGLFunctions_3_3_Core,
    QOpenGLShaderProgram,
    QOpenGLShader,
    QOpenGLVertexArrayObject
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton
)
from OpenGL import GL as gl


# ======================= GLSL =======================

VERT_SRC = r"""
#version 330 core
out vec2 vUV;

void main() {
    const vec2 POS[3] = vec2[3](
        vec2(-1.0, -1.0),
        vec2( 3.0, -1.0),
        vec2(-1.0,  3.0)
    );
    const vec2 UVS[3] = vec2[3](
        vec2(0.0, 0.0),
        vec2(2.0, 0.0),
        vec2(0.0, 2.0)
    );

    vUV = UVS[gl_VertexID];
    gl_Position = vec4(POS[gl_VertexID], 0.0, 1.0);
}
"""

FRAG_SRC = r"""
#version 330 core
in vec2 vUV;
out vec4 FragColor;

uniform sampler2D uTex;
uniform float uStrength; // [0, 1]
uniform float uRadius;   // [0, 1]
uniform float uSoftness; // actual mapped softness: [0.1, 1.0]

void main() {
    vec4 src = texture(uTex, vUV);

    vec2 centered = vUV - vec2(0.5);

    // 角落大致接近 1
    float dist = length(centered) * 1.41421356;

    float inner = clamp(uRadius, 0.0, 1.0);
    float soft = clamp(uSoftness, 0.1, 1.0);

    float vignette = smoothstep(inner, inner + soft, dist);
    float darken = 1.0 - vignette * clamp(uStrength, 0.0, 1.0);

    FragColor = vec4(src.rgb * darken, src.a);
}
"""


# ======================= UI Slider =======================

class VignetteSlider(QWidget):
    valueChanged = Signal(float)

    def __init__(self, title: str, value=0.3, minimum=0.0, maximum=1.0, parent=None):
        super().__init__(parent)
        self.title = title
        self._min = float(minimum)
        self._max = float(maximum)
        self._value = float(value)
        self._dragging = False

        self.setFixedHeight(46)
        self.setCursor(Qt.OpenHandCursor)

        self.c_bg = QColor(45, 45, 45)
        self.c_fill = QColor(48, 110, 210, 210)
        self.c_indicator = QColor(50, 140, 255)
        self.c_tick = QColor(255, 255, 255, 38)
        self.c_text_left = QColor(235, 235, 235)
        self.c_text_right = QColor(170, 170, 170)

    def value(self):
        return self._value

    def setValue(self, v: float):
        self._value = max(self._min, min(self._max, float(v)))
        self.update()
        self.valueChanged.emit(self._value)

    def _ratio(self):
        return (self._value - self._min) / max(1e-8, (self._max - self._min))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect()

        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 7, 7)
        painter.fillPath(path, self.c_bg)

        fill_w = rect.width() * self._ratio()
        painter.setClipPath(path)
        painter.fillRect(QRectF(0, 0, fill_w, rect.height()), self.c_fill)
        painter.setClipping(False)

        painter.setPen(QPen(self.c_tick, 1))
        ticks = 28
        for i in range(ticks + 1):
            x = rect.width() * i / ticks
            h = 7 if i % 4 == 0 else 4
            painter.drawLine(QPointF(x, 0), QPointF(x, h))

        painter.setFont(QFont("Segoe UI", 11))
        painter.setPen(self.c_text_left)
        painter.drawText(rect.adjusted(16, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self.title)

        painter.setPen(self.c_text_right)
        painter.drawText(
            rect.adjusted(0, 0, -14, 0),
            Qt.AlignVCenter | Qt.AlignRight,
            f"{self._value:.2f}"
        )

        x = rect.width() * self._ratio()
        painter.setPen(QPen(self.c_indicator, 2.2))
        painter.drawLine(QPointF(x, 0), QPointF(x, rect.height()))

    def _set_from_x(self, x: float):
        ratio = max(0.0, min(1.0, x / max(1, self.width())))
        self._value = self._min + ratio * (self._max - self._min)
        self.update()
        self.valueChanged.emit(self._value)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            self._set_from_x(event.position().x())

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._set_from_x(event.position().x())

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self.setCursor(Qt.OpenHandCursor)


# ======================= OpenGL Viewer =======================

class GLVignetteViewer(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        self.setFormat(fmt)

        self.gl = None
        self._shader = None
        self._vao = None
        self._tex_id = 0
        self._img = None
        self._uniforms = {}

        self.strength = 0.29
        self.radius = 0.50

        # UI softness 保持 0~1
        self.softness_ui = 0.00

    @staticmethod
    def map_softness(ui_value: float) -> float:
        """
        Map UI softness [0,1] -> actual softness [0.1,1.0]
        """
        ui_value = max(0.0, min(1.0, float(ui_value)))
        return 0.1 + ui_value * 0.9

    def initializeGL(self):
        self.gl = QOpenGLFunctions_3_3_Core()
        self.gl.initializeOpenGLFunctions()

        self._vao = QOpenGLVertexArrayObject(self)
        self._vao.create()
        self._vao.bind()

        prog = QOpenGLShaderProgram(self)
        if not prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT_SRC):
            raise RuntimeError(prog.log())
        if not prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG_SRC):
            raise RuntimeError(prog.log())
        if not prog.link():
            raise RuntimeError(prog.log())

        self._shader = prog
        self._uniforms["uTex"] = prog.uniformLocation("uTex")
        self._uniforms["uStrength"] = prog.uniformLocation("uStrength")
        self._uniforms["uRadius"] = prog.uniformLocation("uRadius")
        self._uniforms["uSoftness"] = prog.uniformLocation("uSoftness")

        gl.glDisable(gl.GL_DEPTH_TEST)

    def resizeGL(self, w, h):
        gl.glViewport(0, 0, w, h)

    def paintGL(self):
        gl.glClearColor(0.10, 0.10, 0.10, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        if self._shader is None or self._tex_id == 0:
            return

        actual_softness = self.map_softness(self.softness_ui)

        self._shader.bind()
        self._vao.bind()

        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._tex_id)

        self.gl.glUniform1i(self._uniforms["uTex"], 0)
        self.gl.glUniform1f(self._uniforms["uStrength"], float(self.strength))
        self.gl.glUniform1f(self._uniforms["uRadius"], float(self.radius))
        self.gl.glUniform1f(self._uniforms["uSoftness"], float(actual_softness))

        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)

        self._vao.release()
        self._shader.release()

    def load_image(self, path: str):
        img = QImage(path)
        if img.isNull():
            return

        self._img = img.convertToFormat(QImage.Format_RGBA8888)
        self._upload_texture()
        self.update()

    def _upload_texture(self):
        if self._img is None:
            return

        if self._tex_id:
            gl.glDeleteTextures(1, np.array([self._tex_id], dtype=np.uint32))

        tex = gl.glGenTextures(1)
        if isinstance(tex, (tuple, list)):
            tex = tex[0]
        self._tex_id = int(tex)

        gl.glBindTexture(gl.GL_TEXTURE_2D, self._tex_id)

        ptr = self._img.constBits()
        nbytes = self._img.sizeInBytes()
        try:
            ptr.setsize(nbytes)
        except Exception:
            pass

        w = self._img.width()
        h = self._img.height()

        gl.glTexImage2D(
            gl.GL_TEXTURE_2D,
            0,
            gl.GL_RGBA8,
            w,
            h,
            0,
            gl.GL_RGBA,
            gl.GL_UNSIGNED_BYTE,
            ptr
        )

        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)

    def set_strength(self, v: float):
        self.strength = max(0.0, min(1.0, float(v)))
        self.update()

    def set_radius(self, v: float):
        self.radius = max(0.0, min(1.0, float(v)))
        self.update()

    def set_softness(self, v: float):
        # 这里保存 UI 值，仍然是 0~1
        self.softness_ui = max(0.0, min(1.0, float(v)))
        self.update()


# ======================= Main Window =======================

class VignetteMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vignette Minimal Demo")
        self.resize(1200, 760)
        self.setStyleSheet("background:#1b1b1b;")

        self.viewer = GLVignetteViewer()

        right_panel = QWidget()
        right_panel.setFixedWidth(360)
        right_panel.setStyleSheet("background:#1f1f1f;")

        panel_layout = QVBoxLayout(right_panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setSpacing(10)

        title_row = QHBoxLayout()

        title = QLabel("◉  Vignette")
        title.setStyleSheet("color:#d8d8d8; font-size:18px; font-weight:600;")

        btn_open = QPushButton("Open Image")
        btn_open.setFixedHeight(30)
        btn_open.setStyleSheet("""
            QPushButton {
                background:#333;
                color:#ddd;
                border:1px solid #555;
                border-radius:6px;
                padding:0 12px;
            }
            QPushButton:hover {
                background:#3c78d8;
                border-color:#3c78d8;
                color:white;
            }
        """)
        btn_open.clicked.connect(self.open_image)

        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(btn_open)

        self.slider_strength = VignetteSlider("Strength", 0.29, minimum=0.0, maximum=1.0)
        self.slider_radius = VignetteSlider("Radius", 0.50, minimum=0.0, maximum=1.0)

        # 注意：这里 UI 滑条仍是 0~1
        self.slider_softness = VignetteSlider("Softness", 0.00, minimum=0.0, maximum=1.0)

        self.slider_strength.valueChanged.connect(self.viewer.set_strength)
        self.slider_radius.valueChanged.connect(self.viewer.set_radius)
        self.slider_softness.valueChanged.connect(self.viewer.set_softness)

        tips = QLabel(
            "参数说明：\n"
            "Strength：边缘压暗强度\n"
            "Radius：晕影开始位置\n"
            "Softness：UI 为 0~1，但实际映射到 0.1~1.0"
        )
        tips.setWordWrap(True)
        tips.setStyleSheet("color:#8f8f8f; font-size:12px; line-height:1.5;")

        panel_layout.addLayout(title_row)
        panel_layout.addSpacing(8)
        panel_layout.addWidget(self.slider_strength)
        panel_layout.addWidget(self.slider_radius)
        panel_layout.addWidget(self.slider_softness)
        panel_layout.addSpacing(10)
        panel_layout.addWidget(tips)
        panel_layout.addStretch()

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.viewer, 1)
        root_layout.addWidget(right_panel)

        self.setCentralWidget(root)

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if path:
            self.viewer.load_image(path)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 9))
    w = VignetteMainWindow()
    w.show()
    sys.exit(app.exec())