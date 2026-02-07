# -*- coding: utf-8 -*-
"""
Selective Color (6 ranges) + PySide6 + OpenGL 3.3 Core
- UI matches provided SelectiveColorWidget (pipette + 6 color buttons + 4 custom sliders)
- Industry-style local color adjustment:
  * RGB->HSL
  * mask = smooth feathered circular hue distance * saturation gate
  * apply hue shift / saturation scale / luminance lift in HSL
  * blend by mask (per-range)
- 6 color ranges store parameters independently
- Eyedropper sets center hue of active range
"""

import sys
import numpy as np

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QLinearGradient, QFont,
    QImage, QSurfaceFormat, QCursor
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFrame, QButtonGroup, QMainWindow, QFileDialog
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import (
    QOpenGLFunctions_3_3_Core, QOpenGLShaderProgram, QOpenGLShader, QOpenGLVertexArrayObject
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

// 6 ranges params
// uRange0[i] = (centerHue, widthHue, hueShift, satAdj)
//   - centerHue: 0..1
//   - widthHue : 0..0.5  (half-width on hue circle)
//   - hueShift : -1..1   (mapped to degrees inside shader)
//   - satAdj   : -1..1   (scale = 1 + satAdj)
uniform vec4 uRange0[6];

// uRange1[i] = (lumAdj, satGateLo, satGateHi, enabled)
//   - lumAdj: -1..1 (mapped inside shader)
//   - satGateLo/Hi: saturation gating thresholds (industry style, avoid neutrals)
//   - enabled: 0/1
uniform vec4 uRange1[6];

float hue_dist(float h1, float h2){
    // circular distance on [0,1)
    float d = abs(h1 - h2);
    return min(d, 1.0 - d);
}

vec3 rgb2hsl(vec3 c){
    float r=c.r, g=c.g, b=c.b;
    float maxc = max(r, max(g,b));
    float minc = min(r, min(g,b));
    float l = (maxc + minc) * 0.5;
    float s = 0.0;
    float h = 0.0;

    float d = maxc - minc;
    if (d > 1e-6){
        s = d / (1.0 - abs(2.0*l - 1.0));
        if (maxc == r){
            h = (g - b) / d;
            h = mod(h, 6.0);
        } else if (maxc == g){
            h = (b - r) / d + 2.0;
        } else {
            h = (r - g) / d + 4.0;
        }
        h /= 6.0;
        if (h < 0.0) h += 1.0;
    }
    return vec3(h, s, l);
}

float hue2rgb(float p, float q, float t){
    if (t < 0.0) t += 1.0;
    if (t > 1.0) t -= 1.0;
    if (t < 1.0/6.0) return p + (q - p) * 6.0 * t;
    if (t < 1.0/2.0) return q;
    if (t < 2.0/3.0) return p + (q - p) * (2.0/3.0 - t) * 6.0;
    return p;
}

vec3 hsl2rgb(vec3 hsl){
    float h=hsl.x, s=hsl.y, l=hsl.z;
    float r,g,b;
    if (s < 1e-6){
        r=g=b=l;
    }else{
        float q = (l < 0.5) ? (l * (1.0 + s)) : (l + s - l*s);
        float p = 2.0*l - q;
        r = hue2rgb(p,q,h + 1.0/3.0);
        g = hue2rgb(p,q,h);
        b = hue2rgb(p,q,h - 1.0/3.0);
    }
    return vec3(r,g,b);
}

float sat_gate(float s, float lo, float hi){
    // industry convention: don't affect near-neutral pixels
    // smooth transition between lo..hi
    return smoothstep(lo, hi, s);
}

float feather_mask(float h, float center, float width){
    // width is half-width; feather is a fraction of width
    float d = hue_dist(h, center);
    float feather = max(0.001, width * 0.50);
    // 1 inside, falloff over [width .. width+feather]
    return 1.0 - smoothstep(width, width + feather, d);
}

vec3 apply_range(vec3 rgb, int i){
    vec3 hsl = rgb2hsl(rgb);

    vec4 p0 = uRange0[i];
    vec4 p1 = uRange1[i];

    float enabled = p1.w;
    if (enabled < 0.5) return rgb;

    float center = p0.x;              // 0..1
    float width  = clamp(p0.y, 0.001, 0.5);
    float hueShiftN = clamp(p0.z, -1.0, 1.0);  // normalized
    float satAdjN   = clamp(p0.w, -1.0, 1.0);  // normalized

    float lumAdjN   = clamp(p1.x, -1.0, 1.0);
    float gateLo    = clamp(p1.y, 0.0, 1.0);
    float gateHi    = clamp(p1.z, 0.0, 1.0);

    float m = feather_mask(hsl.x, center, width);
    m *= sat_gate(hsl.y, gateLo, gateHi);

    if (m < 1e-5) return rgb;

    // --- mapping (industry-ish) ---
    // Hue shift: +/- 30 degrees typical
    float hueShift = hueShiftN * (30.0/360.0);
    // Saturation: scale 0..2 typical
    float satScale = 1.0 + satAdjN; // -1 => 0, +1 => 2
    // Luminance: +/- 0.25 typical
    float lumLift  = lumAdjN * 0.25;

    vec3 hsl2 = hsl;
    hsl2.x = fract(hsl2.x + hueShift);
    hsl2.y = clamp(hsl2.y * satScale, 0.0, 1.0);
    hsl2.z = clamp(hsl2.z + lumLift, 0.0, 1.0);

    vec3 rgb2 = hsl2rgb(hsl2);

    // blend by mask
    return mix(rgb, rgb2, clamp(m, 0.0, 1.0));
}

void main(){
    vec3 c = texture(uTex, vUV).rgb;

    // Apply 6 ranges sequentially (allows overlaps like common editors)
    for (int i=0;i<6;i++){
        c = apply_range(c, i);
    }

    FragColor = vec4(clamp(c, 0.0, 1.0), 1.0);
}
"""


# ======================= UI (match your file, minimal changes) =======================

class PipetteButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)  # minimal change: allow toggle
        self.setStyleSheet("""
            QPushButton {
                background-color: #383838;
                border: 1px solid #555;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #444; }
            QPushButton:pressed { background-color: #222; }
            QPushButton:checked { background-color: #2d3b45; border: 1px solid #6aa2c8; }
        """)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#bbb"), 1.5)
        painter.setPen(pen)
        painter.drawLine(11, 21, 15, 17)
        painter.drawRect(15, 11, 7, 7)
        painter.drawLine(10, 22, 12, 22)
        painter.drawLine(10, 20, 10, 22)


class ColorSelectButton(QPushButton):
    def __init__(self, color_hex, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 26)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.color = QColor(color_hex)
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        if self.isChecked():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#444444"))
            painter.drawRoundedRect(rect, 4, 4)

        block_size = 12
        center_x = rect.width() / 2
        center_y = rect.height() / 2
        color_rect = QRectF(center_x - block_size / 2, center_y - block_size / 2, block_size, block_size)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self.color)
        painter.drawRoundedRect(color_rect, 3, 3)


class CustomSlider(QWidget):
    valueChanged = Signal(float)

    def __init__(self, name: str, parent=None, minimum=-100, maximum=100, initial=0,
                 bg_start="#2c3e4a", bg_end="#4a3e20",
                 fill_neg="#4a90b4", fill_pos="#b4963c"):
        super().__init__(parent)
        self._name = name
        self._min = float(minimum)
        self._max = float(maximum)
        self._value = float(initial)
        self._dragging = False
        self.setFixedHeight(30)
        self.setCursor(Qt.OpenHandCursor)

        self.set_colors(bg_start, bg_end, fill_neg, fill_pos)
        self.c_indicator = QColor(255, 255, 255)

    def set_colors(self, bg_start, bg_end, fill_neg, fill_pos):
        self.c_bg_start = QColor(bg_start)
        self.c_bg_end = QColor(bg_end)
        self.c_fill_neg = QColor(fill_neg)
        self.c_fill_pos = QColor(fill_pos)
        self.update()

    def _value_to_x(self, val):
        ratio = (val - self._min) / (self._max - self._min)
        return ratio * self.width()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())

        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gradient.setColorAt(0, self.c_bg_start)
        gradient.setColorAt(1, self.c_bg_end)
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        painter.fillPath(path, gradient)

        zero_x = self._value_to_x(0) if self._min < 0 else 0
        curr_x = self._value_to_x(self._value)

        current_fill_color = self.c_fill_neg if self._value < 0 else self.c_fill_pos

        if self._min < 0:
            fill_rect = QRectF(min(zero_x, curr_x), 0, abs(curr_x - zero_x), self.height())
        else:
            fill_rect = QRectF(0, 0, curr_x, self.height())

        painter.setOpacity(0.9)
        painter.setClipPath(path)
        painter.fillRect(fill_rect, current_fill_color)
        painter.setClipping(False)
        painter.setOpacity(1.0)

        if self._min < 0 < self._max:
            painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
            painter.drawLine(QPointF(zero_x, 0), QPointF(zero_x, rect.bottom()))

        font = QFont("Segoe UI", 10)
        painter.setFont(font)
        painter.setPen(QColor(230, 230, 230))
        painter.drawText(rect.adjusted(10, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self._name)
        painter.setPen(QColor(255, 255, 255, 200))
        painter.drawText(rect.adjusted(0, 0, -10, 0), Qt.AlignVCenter | Qt.AlignRight, f"{self._value:.2f}")

        painter.setPen(QPen(self.c_indicator, 2))
        painter.drawLine(QPointF(curr_x, 0), QPointF(curr_x, rect.bottom()))

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
        ratio = max(0, min(1, x / max(1, self.width())))
        self._value = self._min + ratio * (self._max - self._min)
        self.valueChanged.emit(self._value)
        self.update()

    def setValue(self, v: float):
        self._value = float(np.clip(v, self._min, self._max))
        self.valueChanged.emit(self._value)
        self.update()

    def value(self) -> float:
        return float(self._value)


class SelectiveColorWidget(QWidget):
    # minimal API for controller
    colorIndexChanged = Signal(int)
    pipetteToggled = Signal(bool)
    paramsChanged = Signal()  # any slider change

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #121212;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Header
        header_layout = QHBoxLayout()
        icon = QLabel(" ∴ ")
        icon.setStyleSheet("color: #d64a6e; font-weight: 900; font-size: 16px;")
        title = QLabel("Selective Color")
        title.setStyleSheet("color: #ddd; font-size: 13px; font-weight: bold;")
        undo_btn = QPushButton("↺")
        undo_btn.setFixedSize(20, 20)
        undo_btn.setStyleSheet("background: transparent; color: #888; border: none;")
        toggle_icon = QLabel("🔵")

        header_layout.addWidget(icon)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(undo_btn)
        header_layout.addWidget(toggle_icon)
        layout.addLayout(header_layout)

        # Tools: Pipette + Colors
        tools_layout = QHBoxLayout()
        tools_layout.setContentsMargins(0, 5, 0, 5)
        tools_layout.setSpacing(0)

        pipette_container = QWidget()
        p_layout = QHBoxLayout(pipette_container)
        p_layout.setContentsMargins(0, 0, 0, 0)
        p_layout.setAlignment(Qt.AlignCenter)
        self.pipette = PipetteButton()
        p_layout.addWidget(self.pipette)

        colors_bg = QFrame()
        colors_bg.setStyleSheet("background-color: #222; border-radius: 6px; border: 1px solid #333;")
        colors_bg.setFixedHeight(34)
        c_layout = QHBoxLayout(colors_bg)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(4)

        # Red, Yellow, Green, Cyan, Blue, Magenta
        self.color_hexes = ["#FF3B30", "#FFCC00", "#28CD41", "#5AC8FA", "#007AFF", "#AF52DE"]
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)
        self.btn_group.idClicked.connect(self._on_color_clicked)

        for i, c_hex in enumerate(self.color_hexes):
            btn = ColorSelectButton(c_hex)
            self.btn_group.addButton(btn, i)
            c_layout.addWidget(btn)

        tools_layout.addWidget(pipette_container, 1)
        tools_layout.addWidget(colors_bg, 0)
        layout.addLayout(tools_layout)

        # Sliders
        self.slider_hue = CustomSlider("Hue")
        self.slider_sat = CustomSlider("Saturation")
        self.slider_lum = CustomSlider("Luminance")
        self.slider_range = CustomSlider("Range", minimum=0, maximum=1.0, initial=1.0,
                                         bg_start="#353535", bg_end="#252525",
                                         fill_neg="#666", fill_pos="#808080")

        layout.addWidget(self.slider_hue)
        layout.addWidget(self.slider_sat)
        layout.addWidget(self.slider_lum)
        layout.addWidget(self.slider_range)
        layout.addStretch()

        self.pipette.toggled.connect(self.pipetteToggled.emit)

        self.slider_hue.valueChanged.connect(lambda _: self.paramsChanged.emit())
        self.slider_sat.valueChanged.connect(lambda _: self.paramsChanged.emit())
        self.slider_lum.valueChanged.connect(lambda _: self.paramsChanged.emit())
        self.slider_range.valueChanged.connect(lambda _: self.paramsChanged.emit())

        # init
        self.btn_group.button(0).setChecked(True)
        self.update_theme(0)

    def _on_color_clicked(self, idx: int):
        self.update_theme(idx)
        self.colorIndexChanged.emit(idx)

    def active_index(self) -> int:
        return int(self.btn_group.checkedId())

    def set_active_index(self, idx: int):
        b = self.btn_group.button(idx)
        if b:
            b.setChecked(True)
            self._on_color_clicked(idx)

    def update_theme(self, color_idx):
        base_c = QColor(self.color_hexes[color_idx])
        dark_base = QColor(base_c)
        dark_base.setAlpha(80)
        bg_dark_hex = dark_base.name()

        sat_bg_start = "#4a4a4a"
        sat_bg_end = bg_dark_hex
        sat_fill_neg = "#607080"
        sat_fill_pos = base_c.name()

        lum_bg_start = "#1a1a1a"
        lum_bg_end = bg_dark_hex
        lum_fill_neg = "#000000"
        lum_fill_pos = "#FFFFFF"

        hue_map = {
            0: ("#AF52DE", "#FFCC00"),
            1: ("#FF3B30", "#28CD41"),
            2: ("#FFCC00", "#5AC8FA"),
            3: ("#28CD41", "#007AFF"),
            4: ("#5AC8FA", "#AF52DE"),
            5: ("#007AFF", "#FF3B30")
        }
        left_hue, right_hue = hue_map.get(color_idx, ("#888", "#888"))

        c_left = QColor(left_hue);  c_left.setAlpha(100)
        c_right = QColor(right_hue); c_right.setAlpha(100)
        hue_bg_start = c_left.name(QColor.HexArgb)
        hue_bg_end = c_right.name(QColor.HexArgb)
        hue_fill_neg = left_hue
        hue_fill_pos = right_hue

        self.slider_hue.set_colors(hue_bg_start, hue_bg_end, hue_fill_neg, hue_fill_pos)
        self.slider_sat.set_colors(sat_bg_start, sat_bg_end, sat_fill_neg, sat_fill_pos)
        self.slider_lum.set_colors(lum_bg_start, lum_bg_end, lum_fill_neg, lum_fill_pos)


# ======================= GL Viewer (Selective Color) =======================

def rgb_to_hue01(r: float, g: float, b: float) -> float:
    mx = max(r, g, b)
    mn = min(r, g, b)
    d = mx - mn
    if d < 1e-8:
        return 0.0
    if mx == r:
        h = (g - b) / d % 6.0
    elif mx == g:
        h = (b - r) / d + 2.0
    else:
        h = (r - g) / d + 4.0
    return (h / 6.0) % 1.0


class GLSelectiveColorViewer(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        self.setFormat(fmt)

        self.gl = None
        self._vao = None
        self._shader = None
        self._uniforms = {}

        self._img = None
        self._tex_id = 0

        self._eyedropper_on = False
        self.on_pick_rgb = None  # callback(QColor)

        # per-range state (6):
        # centerHue01, widthHue01, hueShiftN, satAdjN, lumAdjN
        self.ranges = np.zeros((6, 5), dtype=np.float32)
        self.enabled = np.ones((6,), dtype=np.float32)

        # defaults: typical hue centers for 6 color groups
        # Red=0, Yellow=60, Green=120, Cyan=180, Blue=240, Magenta=300
        centers_deg = np.array([0, 60, 120, 180, 240, 300], dtype=np.float32)
        self.ranges[:, 0] = (centers_deg / 360.0) % 1.0
        # default width: about +/- 30 degrees => 30/360=0.0833
        self.ranges[:, 1] = 30.0 / 360.0
        self.ranges[:, 2] = 0.0  # hueShiftN
        self.ranges[:, 3] = 0.0  # satAdjN
        self.ranges[:, 4] = 0.0  # lumAdjN

        # sat gating (avoid neutrals): default lo=0.05 hi=0.20
        self.sat_gate_lo = 0.05
        self.sat_gate_hi = 0.20

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

        # locations
        self._uniforms["uTex"] = prog.uniformLocation("uTex")
        self._uniforms["uRange0"] = prog.uniformLocation("uRange0")
        self._uniforms["uRange1"] = prog.uniformLocation("uRange1")

        gl.glDisable(gl.GL_DEPTH_TEST)

    def paintGL(self):
        gl.glClearColor(0, 0, 0, 1)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        if not self._shader or self._img is None or self._tex_id == 0:
            return

        self._shader.bind()
        if self._vao:
            self._vao.bind()

        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._tex_id)
        self.gl.glUniform1i(self._uniforms["uTex"], 0)

        # pack uniforms
        # uRange0[i] = (centerHue, widthHue, hueShiftN, satAdjN)
        u0 = np.zeros((6, 4), dtype=np.float32)
        u0[:, 0] = self.ranges[:, 0]
        u0[:, 1] = self.ranges[:, 1]
        u0[:, 2] = self.ranges[:, 2]
        u0[:, 3] = self.ranges[:, 3]

        # uRange1[i] = (lumAdjN, gateLo, gateHi, enabled)
        u1 = np.zeros((6, 4), dtype=np.float32)
        u1[:, 0] = self.ranges[:, 4]
        u1[:, 1] = self.sat_gate_lo
        u1[:, 2] = self.sat_gate_hi
        u1[:, 3] = self.enabled[:]

        # Note: use PyOpenGL glUniform*fv with base location for arrays
        gl.glUniform4fv(self._uniforms["uRange0"], 6, u0)
        gl.glUniform4fv(self._uniforms["uRange1"], 6, u1)

        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)

        if self._vao:
            self._vao.release()
        self._shader.release()

    def load_image(self, path: str) -> bool:
        img = QImage(path)
        if img.isNull():
            return False
        self._img = img.convertToFormat(QImage.Format_RGBA8888)
        self._upload_texture()
        self.update()
        return True

    def _upload_texture(self):
        if self._img is None:
            return

        if self._tex_id:
            gl.glDeleteTextures(1, np.array([self._tex_id], np.uint32))

        tex = gl.glGenTextures(1)
        if isinstance(tex, (list, tuple)):
            tex = tex[0]
        self._tex_id = int(tex)

        gl.glBindTexture(gl.GL_TEXTURE_2D, self._tex_id)
        w, h = self._img.width(), self._img.height()

        ptr = self._img.constBits()
        nbytes = self._img.sizeInBytes()
        try:
            ptr.setsize(nbytes)
        except Exception:
            pass

        gl.glTexImage2D(
            gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, w, h, 0,
            gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, ptr
        )
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)

    def toggle_eyedropper(self, on: bool):
        self._eyedropper_on = bool(on)
        self.setCursor(QCursor(Qt.CrossCursor) if on else QCursor(Qt.ArrowCursor))

    def mousePressEvent(self, e):
        if not self._eyedropper_on or self._img is None:
            return super().mousePressEvent(e)

        if e.button() == Qt.LeftButton:
            uvx = max(0.0, min(1.0, e.position().x() / max(1, self.width())))
            uvy = max(0.0, min(1.0, e.position().y() / max(1, self.height())))
            ix = int(uvx * (self._img.width() - 1))
            iy = int(uvy * (self._img.height() - 1))
            c = self._img.pixelColor(ix, iy)

            if callable(self.on_pick_rgb):
                self.on_pick_rgb(c)

            # auto-off
            self.toggle_eyedropper(False)

    # Controller API
    def set_range_params(self, idx: int, hueShiftN: float, satAdjN: float, lumAdjN: float, widthHue01: float):
        idx = int(np.clip(idx, 0, 5))
        self.ranges[idx, 2] = float(np.clip(hueShiftN, -1.0, 1.0))
        self.ranges[idx, 3] = float(np.clip(satAdjN, -1.0, 1.0))
        self.ranges[idx, 4] = float(np.clip(lumAdjN, -1.0, 1.0))
        self.ranges[idx, 1] = float(np.clip(widthHue01, 0.001, 0.5))
        self.update()

    def set_center_hue(self, idx: int, centerHue01: float):
        idx = int(np.clip(idx, 0, 5))
        self.ranges[idx, 0] = float(centerHue01 % 1.0)
        self.update()


# ======================= Main Window: left UI + right GL viewer =======================

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Selective Color (UI-matched) - GL 3.3 Core")
        self.resize(1280, 760)

        root = QWidget()
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # left panel (exact style)
        self.panel = SelectiveColorWidget()
        self.panel.setFixedWidth(320)

        # add an "Open Image" button above (kept minimal, same styling family)
        left_wrap = QWidget()
        left_lay = QVBoxLayout(left_wrap)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(0)

        topbar = QWidget()
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(12, 10, 12, 10)
        btn_open = QPushButton("Open Image")
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.setStyleSheet("""
            QPushButton { background:#2a2a2a; color:#ddd; border:1px solid #444; padding:6px 10px; border-radius:6px; }
            QPushButton:hover { background:#333; }
        """)
        btn_open.clicked.connect(self.open_image)
        tb.addWidget(btn_open)
        tb.addStretch(1)
        left_lay.addWidget(topbar)
        left_lay.addWidget(self.panel, 1)

        # right viewer
        self.viewer = GLSelectiveColorViewer()

        lay.addWidget(left_wrap, 0)
        lay.addWidget(self.viewer, 1)

        # per-color stored UI values (Hue/Sat/Lum/Range)
        # Hue/Sat/Lum sliders are [-100..100], Range is [0..1]
        self.ui_store = np.zeros((6, 4), dtype=np.float32)
        self.ui_store[:, 3] = 1.0  # range default 1.0

        # connect signals
        self.panel.colorIndexChanged.connect(self.on_color_changed)
        self.panel.paramsChanged.connect(self.on_params_changed)
        self.panel.pipetteToggled.connect(self.viewer.toggle_eyedropper)

        # eyedrop callback
        def on_pick(c: QColor):
            idx = self.panel.active_index()
            r, g, b = c.redF(), c.greenF(), c.blueF()
            hue01 = rgb_to_hue01(r, g, b)
            self.viewer.set_center_hue(idx, hue01)

            # turn off pipette button UI state
            self.panel.pipette.blockSignals(True)
            self.panel.pipette.setChecked(False)
            self.panel.pipette.blockSignals(False)

        self.viewer.on_pick_rgb = on_pick

        # init push
        self.on_color_changed(0)

        self.setStyleSheet("QMainWindow{ background:#0f0f0f; }")

    def open_image(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if fn:
            self.viewer.load_image(fn)

    def on_color_changed(self, idx: int):
        # load stored slider values into UI (so per-color params persist)
        idx = int(idx)
        h, s, l, r = self.ui_store[idx]
        self.panel.slider_hue.setValue(h)
        self.panel.slider_sat.setValue(s)
        self.panel.slider_lum.setValue(l)
        self.panel.slider_range.setValue(r)

        self.push_to_gpu(idx)

    def on_params_changed(self):
        idx = self.panel.active_index()
        self.ui_store[idx, 0] = self.panel.slider_hue.value()
        self.ui_store[idx, 1] = self.panel.slider_sat.value()
        self.ui_store[idx, 2] = self.panel.slider_lum.value()
        self.ui_store[idx, 3] = self.panel.slider_range.value()
        self.push_to_gpu(idx)

    def push_to_gpu(self, idx: int):
        # Map UI -> normalized params for shader
        # Hue slider [-100..100] -> hueShiftN [-1..1]
        hueShiftN = float(np.clip(self.ui_store[idx, 0] / 100.0, -1.0, 1.0))
        # Saturation slider [-100..100] -> satAdjN [-1..1]
        satAdjN = float(np.clip(self.ui_store[idx, 1] / 100.0, -1.0, 1.0))
        # Luminance slider [-100..100] -> lumAdjN [-1..1]
        lumAdjN = float(np.clip(self.ui_store[idx, 2] / 100.0, -1.0, 1.0))

        # Range slider [0..1] -> hue width (half-width) in 0..0.5
        # industry-ish: never let it be exactly zero; map 0..1 -> ~5deg..70deg
        deg = 5.0 + (70.0 - 5.0) * float(np.clip(self.ui_store[idx, 3], 0.0, 1.0))
        widthHue01 = float(np.clip(deg / 360.0, 0.001, 0.5))

        self.viewer.set_range_params(idx, hueShiftN, satAdjN, lumAdjN, widthHue01)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 9))
    win = Main()
    win.show()
    sys.exit(app.exec())
