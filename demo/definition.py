# -*- coding: utf-8 -*-
"""
Definition (Clarity) Adjustment Demo using PySide6 and OpenGL 3.3 Core.
Uses Mipmap-based Local Contrast Enhancement to simulate macOS Photos' Definition.
(Updated: Range 0-1, 1/5th intensity, no negative adjustment)
"""
import sys
import numpy as np
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import (
    QImage, QSurfaceFormat, QIcon, QPixmap, QPainter, QColor, QPen,
    QLinearGradient, QFont, QPainterPath, QCursor
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

# ======================= GLSL shaders =======================
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
uniform float uDefinition; // [0.0, 0.2] internally now (1/5th of original scale)

void main(){
    vec3 color = texture(uTex, vUV).rgb;

    // 如果调整值趋近于0，直接输出原图，节省性能
    if (uDefinition < 0.0001) {
        FragColor = vec4(color, 1.0);
        return;
    }

    // --- Mipmap based Local Contrast Enhancement (Definition/Clarity) ---
    // 提取大范围结构信息
    vec3 blur1 = textureLod(uTex, vUV, 3.0).rgb;
    vec3 blur2 = textureLod(uTex, vUV, 5.0).rgb;
    vec3 blur3 = textureLod(uTex, vUV, 7.0).rgb;

    // 局部均值
    vec3 localMean = (blur1 + blur2 + blur3) / 3.0;

    // 提取高频细节 / 局部对比度
    vec3 detail = color - localMean;

    // 计算当前像素明度，用于生成中间调保护遮罩
    float luma = dot(color, vec3(0.2126, 0.7152, 0.0722));
    float midtoneMask = 1.0 - pow(abs(2.0 * luma - 1.0), 2.0);

    // 放大系数
    float amount = uDefinition * 3.0;

    // 叠加上去：对中间调施加更多的效果，保护极端高光和阴影
    vec3 finalColor = color + detail * amount * (0.3 + 0.7 * midtoneMask);

    // 防止溢出
    finalColor = clamp(finalColor, 0.0, 1.0);
    FragColor = vec4(finalColor, 1.0);
}
"""


# ======================= Styled Components =======================
class DefinitionSlider(QWidget):
    """Custom slider for Definition with 0-1 range and single directional fill"""

    valueChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min = 0.0
        self._max = 1.0
        self._value = 0.0
        self._dragging = False
        self.setFixedHeight(34)
        self.setCursor(Qt.OpenHandCursor)

        # Track and fill colors
        self.c_bg_dark = QColor(40, 40, 40)
        self.c_bg_light = QColor(70, 70, 70)
        self.c_indicator = QColor(255, 255, 255)
        self.c_tick = QColor(255, 255, 255, 60)

        # 只保留正向的高亮颜色
        self.c_fill = QColor(220, 220, 220, 200)

    def _normalised_value(self):
        return (self._value - self._min) / (self._max - self._min)

    def _value_to_x(self, val):
        ratio = (val - self._min) / (self._max - self._min)
        return ratio * self.width()

    def value(self):
        return self._value

    def setValue(self, v):
        self._value = max(self._min, min(self._max, float(v)))
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        # 1. Background
        gradient = QLinearGradient(0, 0, 0, rect.height())
        gradient.setColorAt(0, self.c_bg_dark)
        gradient.setColorAt(1, self.c_bg_light)
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        painter.fillPath(path, gradient)

        # 2. Highlight fill block (始终从最左侧0开始填充到当前值)
        curr_x = self._value_to_x(self._value)
        fill_rect = QRectF(0, 0, curr_x, self.height())

        # 为了让圆角背景不被直角填充块破坏，做一个 Clip Path
        painter.setClipPath(path)
        painter.fillRect(fill_rect, self.c_fill)
        painter.setClipping(False)  # 恢复

        # 3. Tick marks
        painter.setPen(QPen(self.c_tick, 1))
        ticks = 50
        for i in range(ticks + 1):
            x = (i / ticks) * rect.width()
            h = 6 if i % 5 == 0 else 3
            painter.drawLine(QPointF(x, 0), QPointF(x, h))

        # 4. Label and value
        font = QFont("Inter", 12, QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(240, 240, 240))
        painter.drawText(rect.adjusted(12, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, "Definition")

        painter.setPen(QColor(255, 255, 255, 160))
        # 显示为两位小数，例如 0.00 ~ 1.00
        painter.drawText(rect.adjusted(0, 0, -12, 0), Qt.AlignVCenter | Qt.AlignRight, f"{self._value:.2f}")

        # 5. Position indicator
        handle_x = self._normalised_value() * rect.width()
        painter.setPen(QPen(self.c_indicator, 2))
        painter.drawLine(QPointF(handle_x, 0), QPointF(handle_x, rect.bottom()))

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
        self.valueChanged.emit(self._value)

    def _update_from_pos(self, x):
        ratio = max(0, min(1, x / self.width()))
        self._value = self._min + ratio * (self._max - self._min)
        self.valueChanged.emit(self._value)
        self.update()


# ======================= OpenGL viewer =======================
class GLDefinitionViewer(QOpenGLWidget):
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

        self.definition = 0.0

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
        self._uniforms["uDefinition"] = prog.uniformLocation("uDefinition")

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
        self.gl.glUniform1f(self._uniforms["uDefinition"], float(self.definition))

        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)

        if self._vao: self._vao.release()
        self._shader.release()

    def load_image(self, path: str):
        img = QImage(path)
        if img.isNull():
            return
        self._img = img.convertToFormat(QImage.Format_RGBA8888)
        self._upload_texture()
        self.definition = 0.0
        self.update()

    def _upload_texture(self):
        if self._img is None:
            return
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
        gl.glGenerateMipmap(gl.GL_TEXTURE_2D)

        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR_MIPMAP_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)

    def set_definition(self, v: float):
        # UI 传入的 v 范围是 0.0 ~ 1.0
        # 以前 100 对应内部 1.0；现在要求新的 1.0 对应以前的 20 (即 1/5 强度)
        # 20 / 100 = 0.2，所以这里将 UI 传入的值乘以 0.2。
        self.definition = v * 0.2
        self.update()


# ======================= Main Window =======================
class DefinitionMain(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Definition Adjustment")
        self.resize(1100, 720)
        self.setStyleSheet("background-color: #1a1a1a;")

        self.viewer = GLDefinitionViewer()

        # Control panel
        panel = QWidget()
        panel.setStyleSheet("background-color: #1a1a1a;")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(15, 15, 15, 15)
        panel_layout.setSpacing(20)

        # Header
        header = QHBoxLayout()
        title_lbl = QLabel("Structure & Detail")
        title_lbl.setStyleSheet("color: #eee; font-weight: bold; font-size: 16px;")

        open_btn = QPushButton("OPEN IMAGE")
        open_btn.setFixedHeight(28)
        open_btn.setStyleSheet("""
            QPushButton { 
                background-color: #333; color: #ddd; border: 1px solid #555; 
                border-radius: 6px; font-size: 11px; font-weight: bold; padding: 0 10px;
            }
            QPushButton:hover { background-color: #4a90e2; border-color: #4a90e2; color: white;}
        """)
        open_btn.clicked.connect(self.open_image)

        header.addWidget(title_lbl)
        header.addStretch()
        header.addWidget(open_btn)

        # Definition Slider
        self.slider = DefinitionSlider()

        # Info text updated to reflect new behavior
        info_lbl = QLabel("Slide right to delicately enhance local contrast, texture and structure.")
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
        self.slider.valueChanged.connect(self.viewer.set_definition)

    def open_image(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if fn:
            self.viewer.load_image(fn)
            self.slider.setValue(0.0)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Segoe UI", 9)
    app.setFont(font)
    win = DefinitionMain()
    win.show()
    sys.exit(app.exec())