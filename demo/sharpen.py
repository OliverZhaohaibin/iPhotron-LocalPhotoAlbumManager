# -*- coding: utf-8 -*-
"""
Minimal Sharpen Demo using PySide6 + OpenGL 3.3 Core

Features based on industry standards (Unsharp Mask with Edge Masking):
- Intensity: Overall sharpening amount [0, 1] mapped to [0, 5.0] internally
- Edges: Threshold for local contrast masking to protect flat areas [0, 1]
- Falloff: Smoothness of the edge mask transition [0, 1]

Run:
    pip install PySide6 PyOpenGL numpy
    python sharpen_demo.py
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
uniform float uIntensity; // [0, 1] mapped to larger multiplier internally
uniform float uEdges;     // [0, 1] edge detection threshold
uniform float uFalloff;   // [0, 1] transition smoothness

void main() {
    vec2 texSize = vec2(textureSize(uTex, 0));
    vec2 texel = 1.0 / texSize;

    // 1. Sample 3x3 neighborhood
    vec3 c00 = texture(uTex, vUV + vec2(-texel.x, -texel.y)).rgb;
    vec3 c10 = texture(uTex, vUV + vec2( 0.0,     -texel.y)).rgb;
    vec3 c20 = texture(uTex, vUV + vec2( texel.x, -texel.y)).rgb;

    vec3 c01 = texture(uTex, vUV + vec2(-texel.x,  0.0)).rgb;
    vec3 c11 = texture(uTex, vUV).rgb; // Center pixel
    vec3 c21 = texture(uTex, vUV + vec2( texel.x,  0.0)).rgb;

    vec3 c02 = texture(uTex, vUV + vec2(-texel.x,  texel.y)).rgb;
    vec3 c12 = texture(uTex, vUV + vec2( 0.0,      texel.y)).rgb;
    vec3 c22 = texture(uTex, vUV + vec2( texel.x,  texel.y)).rgb;

    // 2. Calculate blur (Approximate Gaussian)
    vec3 blur = c11 * 0.25 + 
                (c10 + c01 + c21 + c12) * 0.125 + 
                (c00 + c20 + c02 + c22) * 0.0625;

    // 3. Extract high-pass detail (Unsharp Mask)
    vec3 highPass = c11 - blur;

    // 4. Calculate local luminance contrast for edge detection
    vec3 lumaCoef = vec3(0.299, 0.587, 0.114);
    float lum00 = dot(c00, lumaCoef);
    float lum10 = dot(c10, lumaCoef);
    float lum20 = dot(c20, lumaCoef);
    float lum01 = dot(c01, lumaCoef);
    float lum11 = dot(c11, lumaCoef);
    float lum21 = dot(c21, lumaCoef);
    float lum02 = dot(c02, lumaCoef);
    float lum12 = dot(c12, lumaCoef);
    float lum22 = dot(c22, lumaCoef);

    float lMin = min(lum00, min(lum10, min(lum20, min(lum01, min(lum11, min(lum21, min(lum02, min(lum12, lum22))))))));
    float lMax = max(lum00, max(lum10, max(lum20, max(lum01, max(lum11, max(lum21, max(lum02, max(lum12, lum22))))))));
    float localContrast = lMax - lMin;

    // 5. Edge Masking based on uEdges and uFalloff
    // uEdges sets the base contrast required to be considered an edge (max mapped to ~0.4 luma diff)
    // uFalloff sets the smoothstep band width
    float threshold = uEdges * 0.4;
    float band = max(uFalloff * 0.4, 0.001);

    // Mask approaches 1.0 at strong edges, 0.0 at flat areas.
    float mask = smoothstep(threshold, threshold + band, localContrast);

    // 6. Apply Sharpening
    float amount = uIntensity * 5.0; // Map UI [0,1] to actual sharpening multiplier
    vec3 sharpened = c11 + (highPass * amount * mask);

    // Standard clamp to avoid RGB overflow/underflow artifacts
    sharpened = clamp(sharpened, 0.0, 1.0);

    FragColor = vec4(sharpened, 1.0);
}
"""


# ======================= UI Slider =======================

class DemoSlider(QWidget):
    valueChanged = Signal(float)

    def __init__(self, title: str, value=0.0, minimum=0.0, maximum=1.0, parent=None):
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

class GLSharpenViewer(QOpenGLWidget):
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

        # Default values from the provided screenshot
        self.intensity = 0.81
        self.edges = 0.22
        self.falloff = 0.69

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
        self._uniforms["uIntensity"] = prog.uniformLocation("uIntensity")
        self._uniforms["uEdges"] = prog.uniformLocation("uEdges")
        self._uniforms["uFalloff"] = prog.uniformLocation("uFalloff")

        gl.glDisable(gl.GL_DEPTH_TEST)

    def resizeGL(self, w, h):
        gl.glViewport(0, 0, w, h)

    def paintGL(self):
        gl.glClearColor(0.10, 0.10, 0.10, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        if self._shader is None or self._tex_id == 0:
            return

        self._shader.bind()
        self._vao.bind()

        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._tex_id)

        self.gl.glUniform1i(self._uniforms["uTex"], 0)
        self.gl.glUniform1f(self._uniforms["uIntensity"], float(self.intensity))
        self.gl.glUniform1f(self._uniforms["uEdges"], float(self.edges))
        self.gl.glUniform1f(self._uniforms["uFalloff"], float(self.falloff))

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

    def set_intensity(self, v: float):
        self.intensity = max(0.0, min(1.0, float(v)))
        self.update()

    def set_edges(self, v: float):
        self.edges = max(0.0, min(1.0, float(v)))
        self.update()

    def set_falloff(self, v: float):
        self.falloff = max(0.0, min(1.0, float(v)))
        self.update()


# ======================= Main Window =======================

class SharpenMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sharpen Minimal Demo")
        self.resize(1200, 760)
        self.setStyleSheet("background:#1b1b1b;")

        self.viewer = GLSharpenViewer()

        right_panel = QWidget()
        right_panel.setFixedWidth(360)
        right_panel.setStyleSheet("background:#1f1f1f;")

        panel_layout = QVBoxLayout(right_panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setSpacing(10)

        title_row = QHBoxLayout()

        title = QLabel("◉  Sharpen")
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

        # Initialize sliders with values exactly from the provided screenshot
        self.slider_intensity = DemoSlider("Intensity", 0.81, minimum=0.0, maximum=1.0)
        self.slider_edges = DemoSlider("Edges", 0.22, minimum=0.0, maximum=1.0)
        self.slider_falloff = DemoSlider("Falloff", 0.69, minimum=0.0, maximum=1.0)

        self.slider_intensity.valueChanged.connect(self.viewer.set_intensity)
        self.slider_edges.valueChanged.connect(self.viewer.set_edges)
        self.slider_falloff.valueChanged.connect(self.viewer.set_falloff)

        tips = QLabel(
            "参数说明：\n"
            "• Intensity：锐化总强度，控制高频细节的放大倍数。\n"
            "• Edges：边缘遮罩阈值。数值越大，代表只针对对比度越强的边缘进行锐化，避免放大平坦区域的噪点。\n"
            "• Falloff：边缘遮罩的平滑/衰减度，避免产生生硬的边界断层。"
        )
        tips.setWordWrap(True)
        tips.setStyleSheet("color:#8f8f8f; font-size:12px; line-height:1.6;")

        panel_layout.addLayout(title_row)
        panel_layout.addSpacing(8)
        panel_layout.addWidget(self.slider_intensity)
        panel_layout.addWidget(self.slider_edges)
        panel_layout.addWidget(self.slider_falloff)
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
    w = SharpenMainWindow()
    w.show()
    sys.exit(app.exec())