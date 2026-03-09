# -*- coding: utf-8 -*-
"""
Noise Reduction Demo using PySide6 and OpenGL 3.3 Core.
Uses Bilateral Filter (Edge-Preserving Smoothing) for standard noise reduction.
Supports extending the slider maximum from 2.0 to 5.0 by holding Option/Alt.
"""
import sys
import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QTimer
from PySide6.QtGui import (
    QImage, QSurfaceFormat, QPainter, QColor, QPen,
    QLinearGradient, QFont, QPainterPath
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import (
    QOpenGLFunctions_3_3_Core, QOpenGLShaderProgram, QOpenGLShader, QOpenGLVertexArrayObject
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton
)
from OpenGL import GL as gl

# ======================= GLSL Shaders =======================
VERT_SRC = r"""
#version 330 core
out vec2 vUV;
void main() {
    // Fullscreen triangle technique (avoids VBO binding)
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
uniform float uAmount; // [0.0, 5.0] from UI (dynamically extended)

// Bilateral filter configuration
const int RADIUS = 3; 
const float SIGMA_SPACE = 1.5; 

void main(){
    vec3 centerColor = texture(uTex, vUV).rgb;

    // Early exit if amount is extremely low to save performance
    if (uAmount < 0.005) {
        FragColor = vec4(centerColor, 1.0);
        return;
    }

    vec2 texSize = vec2(textureSize(uTex, 0));
    vec2 invTexSize = 1.0 / texSize;

    // Map UI Amount to Color Sigma
    float sigmaColor = max(uAmount * 0.075, 0.001);

    vec3 resultColor = vec3(0.0);
    float totalWeight = 0.0;

    // Standard single-pass Bilateral Filter
    for (int y = -RADIUS; y <= RADIUS; ++y) {
        for (int x = -RADIUS; x <= RADIUS; ++x) {
            vec2 offset = vec2(float(x), float(y));
            vec3 sampleColor = texture(uTex, vUV + offset * invTexSize).rgb;

            // 1. Spatial Weight
            float spaceDist2 = dot(offset, offset);
            float spaceWeight = exp(-spaceDist2 / (2.0 * SIGMA_SPACE * SIGMA_SPACE));

            // 2. Color/Range Weight
            vec3 colorDiff = sampleColor - centerColor;
            float colorDist2 = dot(colorDiff, colorDiff);
            float colorWeight = exp(-colorDist2 / (2.0 * sigmaColor * sigmaColor));

            // Combined Weight
            float weight = spaceWeight * colorWeight;

            resultColor += sampleColor * weight;
            totalWeight += weight;
        }
    }

    FragColor = vec4(resultColor / totalWeight, 1.0);
}
"""


# ======================= Styled Components =======================
class NoiseReductionSlider(QWidget):
    """Custom slider supporting Option/Alt to extend max range dynamically."""

    valueChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min = 0.0
        self._max_normal = 2.0
        self._max_extended = 5.0
        self._value = 1.03

        # 拖拽防跳跃锁
        self._dragging = False
        self._drag_extended = False

        self.setFixedHeight(34)
        self.setCursor(Qt.OpenHandCursor)

        # Colors derived from the screenshot
        self.c_bg = QColor(42, 42, 42)
        self.c_fill = QColor(48, 90, 150, 240)
        self.c_indicator = QColor(30, 130, 255)
        self.c_tick = QColor(255, 255, 255, 40)
        self.c_text_left = QColor(180, 200, 220)
        self.c_text_right = QColor(255, 255, 255)

        # Timer: 轮询键盘状态，保证不点鼠标时按下 Option 键也能有实时的视觉缩放反馈
        self._last_max = self.current_max
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_modifiers)
        self._timer.start(50)  # 20Hz 刷新率

    def _check_modifiers(self):
        c_max = self.current_max
        if c_max != self._last_max:
            self._last_max = c_max
            self.update()

    @property
    def current_max(self):
        """动态计算当前的有效最大值"""
        # 1. 拖拽期间，如果触发过扩展模式，则锁定为扩展量程以防跳跃
        if self._dragging and self._drag_extended:
            return self._max_extended
        # 2. 如果数值已经超过了常规最大值，维持扩展量程防止 UI 溢出
        if self._value > self._max_normal:
            return self._max_extended
        # 3. 如果用户正在按住 Option(Mac) / Alt(Win) 键
        if QApplication.keyboardModifiers() & Qt.AltModifier:
            return self._max_extended
        # 4. 默认正常模式
        return self._max_normal

    def _normalised_value(self):
        c_max = self.current_max
        return (self._value - self._min) / (c_max - self._min)

    def _value_to_x(self, val):
        c_max = self.current_max
        ratio = (val - self._min) / (c_max - self._min)
        return ratio * self.width()

    def value(self):
        return self._value

    def setValue(self, v):
        self._value = max(self._min, min(self._max_extended, float(v)))
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        # 1. Background
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        painter.fillPath(path, self.c_bg)

        # 2. Highlight fill block (Blue fill)
        curr_x = self._value_to_x(self._value)
        fill_rect = QRectF(0, 0, curr_x, self.height())

        painter.setClipPath(path)
        painter.fillRect(fill_rect, self.c_fill)
        painter.setClipping(False)

        # 3. Tick marks (top edge)
        painter.setPen(QPen(self.c_tick, 1))
        ticks = 40
        for i in range(ticks + 1):
            x = (i / ticks) * rect.width()
            h = 5 if i % 5 == 0 else 3
            painter.drawLine(QPointF(x, 0), QPointF(x, h))

        # 4. Label and value text
        font = QFont("Inter", 12, QFont.Weight.Medium)
        painter.setFont(font)

        painter.setPen(self.c_text_left)
        painter.drawText(rect.adjusted(12, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, "Amount")

        painter.setPen(self.c_text_right)
        painter.drawText(rect.adjusted(0, 0, -12, 0), Qt.AlignVCenter | Qt.AlignRight, f"{self._value:.2f}")

        # 5. Position indicator
        handle_x = self._normalised_value() * rect.width()
        painter.setPen(QPen(self.c_indicator, 2.5))
        painter.drawLine(QPointF(handle_x, 0), QPointF(handle_x, rect.bottom()))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)

            # 记录按下时是否需要进入/维持扩展模式锁
            self._drag_extended = (self._value > self._max_normal) or (event.modifiers() & Qt.AltModifier)
            self._update_from_pos(event.position().x())

    def mouseMoveEvent(self, event):
        if self._dragging:
            # 拖拽中途按下 Option/Alt 键，亦可激活扩展模式
            if event.modifiers() & Qt.AltModifier:
                self._drag_extended = True
            self._update_from_pos(event.position().x())

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self.setCursor(Qt.OpenHandCursor)
        self.valueChanged.emit(self._value)
        self.update()

    def _update_from_pos(self, x):
        c_max = self.current_max
        ratio = max(0, min(1, x / self.width()))
        self._value = self._min + ratio * (c_max - self._min)
        self.valueChanged.emit(self._value)
        self.update()


# ======================= OpenGL viewer =======================
class GLNoiseViewer(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        self.setFormat(fmt)

        self._img = None
        self._tex_id = 0
        self._shader = None
        self._vao = None
        self._uniforms = {}

        self.amount = 1.03

    def initializeGL(self):
        self.gl = QOpenGLFunctions_3_3_Core()
        self.gl.initializeOpenGLFunctions()
        self._vao = QOpenGLVertexArrayObject(self)
        self._vao.create()
        self._vao.bind()

        prog = QOpenGLShaderProgram(self)
        prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT_SRC)
        prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG_SRC)
        if not prog.link():
            raise RuntimeError("Shader link failed: " + prog.log())
        self._shader = prog

        self._uniforms["uTex"] = prog.uniformLocation("uTex")
        self._uniforms["uAmount"] = prog.uniformLocation("uAmount")

        gl.glDisable(gl.GL_DEPTH_TEST)

    def paintGL(self):
        gl.glClearColor(0.1, 0.1, 0.1, 1)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        if not self._shader or self._img is None or self._tex_id == 0:
            return

        self._shader.bind()
        if self._vao: self._vao.bind()

        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._tex_id)
        self.gl.glUniform1i(self._uniforms["uTex"], 0)
        self.gl.glUniform1f(self._uniforms["uAmount"], float(self.amount))

        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)

        if self._vao: self._vao.release()
        self._shader.release()

    def load_image(self, path: str):
        img = QImage(path)
        if img.isNull():
            return
        self._img = img.convertToFormat(QImage.Format_RGBA8888)
        self._upload_texture()
        self.update()

    def _upload_texture(self):
        if self._img is None: return
        if self._tex_id:
            gl.glDeleteTextures(1, np.array([self._tex_id], np.uint32))

        tex = gl.glGenTextures(1)
        if isinstance(tex, (list, tuple)): tex = tex[0]
        self._tex_id = int(tex)

        gl.glBindTexture(gl.GL_TEXTURE_2D, self._tex_id)
        w, h = self._img.width(), self._img.height()
        ptr = self._img.constBits()
        nbytes = self._img.sizeInBytes()
        try:
            ptr.setsize(nbytes)
        except Exception:
            pass

        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, w, h, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, ptr)

        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)

    def set_amount(self, v: float):
        self.amount = v
        self.update()


# ======================= Main Window =======================
class NoiseReductionMain(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Noise Reduction Adjustment")
        self.resize(1100, 720)
        self.setStyleSheet("background-color: #1a1a1a;")

        self.viewer = GLNoiseViewer()

        # Control panel
        panel = QWidget()
        panel.setStyleSheet("background-color: #1e1e1e;")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(20, 20, 20, 20)
        panel_layout.setSpacing(15)

        # Header
        header = QHBoxLayout()
        title_lbl = QLabel("Noise Reduction")
        title_lbl.setStyleSheet("color: #ddd; font-weight: bold; font-size: 16px;")

        open_btn = QPushButton("OPEN IMAGE")
        open_btn.setFixedHeight(28)
        open_btn.setStyleSheet("""
            QPushButton { 
                background-color: #333; color: #ddd; border: 1px solid #555; 
                border-radius: 6px; font-size: 11px; font-weight: bold; padding: 0 10px;
            }
            QPushButton:hover { background-color: #3082ff; border-color: #3082ff; color: white;}
        """)
        open_btn.clicked.connect(self.open_image)

        header.addWidget(title_lbl)
        header.addStretch()
        header.addWidget(open_btn)

        # Noise Reduction Slider
        self.slider = NoiseReductionSlider()
        self.viewer.set_amount(self.slider.value())

        # Updated text to hint about the Option key
        info_lbl = QLabel(
            "Smooths out luminance and color noise while preserving sharp edges using a Bilateral Filter.\n\nTip: Hold Option (Mac) or Alt (Win) to extend the maximum range to 5.0.")
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #888; font-size: 12px; line-height: 1.4;")

        panel_layout.addLayout(header)
        panel_layout.addWidget(self.slider)
        panel_layout.addWidget(info_lbl)
        panel_layout.addStretch()

        # Main layout
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.viewer, 1)
        main_layout.addWidget(panel)
        panel.setFixedWidth(340)

        central = QWidget()
        central.setLayout(main_layout)
        self.setCentralWidget(central)

        # Connect slider
        self.slider.valueChanged.connect(self.viewer.set_amount)

    def open_image(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if fn:
            self.viewer.load_image(fn)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Segoe UI", 9)
    app.setFont(font)
    win = NoiseReductionMain()
    win.show()
    sys.exit(app.exec())