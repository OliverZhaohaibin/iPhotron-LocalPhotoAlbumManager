# -*- coding: utf-8 -*-
"""
Minimal standalone GPU White Balance demo.
- Modes: Neutral Gray / Skin Tone / Temperature/Tint
- Eyedropper tool (pick from image)
- One Warmth slider for fine-tuning
"""
import sys, numpy as np
from PySide6.QtCore import Qt, QPoint, QSize
from PySide6.QtGui import QImage, QSurfaceFormat, QCursor
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import (
    QOpenGLFunctions_3_3_Core, QOpenGLShaderProgram, QOpenGLShader, QOpenGLVertexArrayObject
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QPushButton, QComboBox
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
uniform vec3  uGain;     // per-channel gains (computed on CPU)
uniform float uWarmth;   // [-1,1]  negative=cooler(偏蓝)  positive=warmer(偏黄)

// 简单的色温微调：对 R/B 做相反缩放，再归一化到平均=1，保持亮度大致不变
vec3 warmth_adjust(vec3 c, float w){
    if (w == 0.0) return c;
    // max scale ~ ±30%
    float k = 0.30 * w;
    vec3 g = vec3(1.0 + k, 1.0, 1.0 - k);
    g /= (g.r + g.g + g.b) / 3.0; // 归一化到平均增益=1
    return c * g;
}

void main(){
    vec3 color = texture(uTex, vUV).rgb;
    // 先应用 CPU 估计的通道增益，再用暖色微调
    color = color * uGain;
    color = warmth_adjust(color, uWarmth);
    color = clamp(color, 0.0, 1.0);
    FragColor = vec4(color, 1.0);
}
"""


# ======================= OpenGL viewer =======================
class GLWBViewer(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        self.setFormat(fmt)

        self._img = None            # QImage RGBA8888
        self._tex_id = 0
        self._shader = None
        self._vao = None
        self._uniforms = {}

        self.mode = "Neutral Gray"  # or "Skin Tone" / "Temperature/Tint"
        self.gain = np.array([1.0, 1.0, 1.0], dtype=np.float32)
        self.warmth = 0.0

        self._eyedropper_on = False

        # for aspect-agnostic mapping, we keep it simple: stretch to widget (full-bleed)
        self.setMouseTracking(True)

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
        for n in ["uTex", "uGain", "uWarmth"]:
            self._uniforms[n] = prog.uniformLocation(n)
        gl.glDisable(gl.GL_DEPTH_TEST)

    def paintGL(self):
        gl.glClearColor(0, 0, 0, 1)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        if not self._shader or self._img is None or self._tex_id == 0:
            return
        self._shader.bind()
        if self._vao: self._vao.bind()
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._tex_id)
        self.gl.glUniform1i(self._uniforms["uTex"], 0)
        self.gl.glUniform3f(self._uniforms["uGain"], float(self.gain[0]), float(self.gain[1]), float(self.gain[2]))
        self.gl.glUniform1f(self._uniforms["uWarmth"], float(self.warmth))
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)
        if self._vao: self._vao.release()
        self._shader.release()

    def load_image(self, path: str):
        img = QImage(path)
        if img.isNull():
            return
        self._img = img.convertToFormat(QImage.Format_RGBA8888)
        self._upload_texture()
        # reset gain & warmth
        self.gain[:] = 1.0
        self.warmth = 0.0
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
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)

    # ---------- eyedropper ----------
    def set_mode(self, mode: str):
        self.mode = mode
        self.update()

    def set_warmth(self, v: float):
        self.warmth = v
        self.update()

    def toggle_eyedropper(self, on: bool):
        self._eyedropper_on = on
        self.setCursor(QCursor(Qt.CrossCursor) if on else QCursor(Qt.ArrowCursor))

    def mousePressEvent(self, e):
        if not self._eyedropper_on or self._img is None:
            return super().mousePressEvent(e)

        if e.button() == Qt.LeftButton:
            # widget坐标 -> UV (0..1)，画面拉伸满铺（最小demo）
            uvx = max(0.0, min(1.0, e.position().x() / max(1, self.width())))
            uvy = max(0.0, min(1.0, e.position().y() / max(1, self.height())))
            # 纹理坐标 -> 图像像素
            ix = int(uvx * (self._img.width() - 1))
            iy = int(uvy * (self._img.height() - 1))
            rgba = self._img.pixelColor(ix, iy)
            r = rgba.red()   / 255.0
            g = rgba.green() / 255.0
            b = rgba.blue()  / 255.0
            self._apply_pick(np.array([r, g, b], dtype=np.float32))
            self.toggle_eyedropper(False)

    # 计算不同模式下的增益
    def _apply_pick(self, rgb: np.ndarray):
        eps = 1e-6
        rgb = np.clip(rgb, eps, 1.0)

        if self.mode == "Neutral Gray":
            # 让取样点变为灰：RGB相等
            gray = float(rgb.mean())
            gain = np.array([gray / rgb[0], gray / rgb[1], gray / rgb[2]], dtype=np.float32)

        elif self.mode == "Skin Tone":
            # 目标肤色比例（非常简化的经验值），保持亮度后归一化
            target = np.array([1.10, 1.00, 0.90], dtype=np.float32)
            target /= target.mean()
            gain = target / rgb
            # 亮度保持（用 BT.709 亮度）
            w = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
            Y_src = float((w * rgb).sum())
            Y_new = float((w * (rgb * gain)).sum()) + eps
            gain *= (Y_src / Y_new)

        else:  # "Temperature/Tint"：不依赖取样；用当前暖色滑条做调节，这里保持 1:1
            gain = np.array([1.0, 1.0, 1.0], dtype=np.float32)

        # 把增益归一化到均值=1，避免过多改变整体曝光
        gain /= float(gain.mean() + eps)
        self.gain = gain.astype(np.float32)
        self.update()


# ======================= GUI MainWindow =======================
class WBMain(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GPU White Balance Demo")
        self.resize(1100, 720)

        self.viewer = GLWBViewer()

        # 顶部工具栏区域
        top = QWidget()
        th = QHBoxLayout(top)
        th.setContentsMargins(6, 6, 6, 6)
        th.setSpacing(8)

        open_btn = QPushButton("Open Image")
        open_btn.clicked.connect(self.open_image)

        self.mode_cb = QComboBox()
        self.mode_cb.addItems(["Neutral Gray", "Skin Tone", "Temperature/Tint"])
        self.mode_cb.currentTextChanged.connect(self.on_mode_changed)

        self.eyedrop_btn = QPushButton("Eyedropper")
        self.eyedrop_btn.setCheckable(True)
        self.eyedrop_btn.toggled.connect(self.viewer.toggle_eyedropper)

        # Warmth 滑条（-100..100 -> -1..1）
        warmth_label = QLabel("Warmth: +0.00")
        warmth_slider = QSlider(Qt.Horizontal)
        warmth_slider.setRange(-100, 100)
        warmth_slider.setValue(0)
        def on_warmth(v):
            f = v / 100.0
            warmth_label.setText(f"Warmth: {f:+.2f}")
            self.viewer.set_warmth(f if self.mode_cb.currentText() != "Neutral Gray" or True else f)
        warmth_slider.valueChanged.connect(on_warmth)

        th.addWidget(open_btn)
        th.addSpacing(12)
        th.addWidget(QLabel("Method:"))
        th.addWidget(self.mode_cb)
        th.addWidget(self.eyedrop_btn)
        th.addSpacing(18)
        th.addWidget(warmth_label)
        th.addWidget(warmth_slider, 1)
        th.addStretch(1)

        # 主布局
        root = QVBoxLayout()
        root.addWidget(top)
        root.addWidget(self.viewer, 1)

        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

        # 初始小图（可选）
        # self.viewer.load_image("your_image.jpg")

    def on_mode_changed(self, text: str):
        self.viewer.set_mode(text)
        # Temperature/Tint 模式通常不需要吸管
        self.eyedrop_btn.setChecked(False)
        self.eyedrop_btn.setEnabled(text in ("Neutral Gray", "Skin Tone"))

    def open_image(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if fn:
            self.viewer.load_image(fn)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = WBMain()
    win.show()
    sys.exit(app.exec())
