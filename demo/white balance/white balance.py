# -*- coding: utf-8 -*-
"""
Merged GPU White Balance with warmth-ui styling.
Fixes blue bias issue and integrates icons.
"""
import sys, numpy as np
from pathlib import Path
from PySide6.QtCore import Qt, QPoint, QSize, Signal, QPointF
from PySide6.QtGui import QImage, QSurfaceFormat, QCursor, QIcon, QPixmap, QPainter, QColor, QPen, QLinearGradient, QFont, QPainterPath
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import (
    QOpenGLFunctions_3_3_Core, QOpenGLShaderProgram, QOpenGLShader, QOpenGLVertexArrayObject
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QPushButton, QComboBox, QFrame
)
from PySide6.QtSvgWidgets import QSvgWidget
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
uniform float uTemperature; // Kelvin temperature offset (normalized to [-1,1])
uniform float uTint;        // Tint offset [-1,1] green(-) to magenta(+)

// Improved warmth adjustment with proper normalization
vec3 warmth_adjust(vec3 c, float w){
    if (w == 0.0) return c;
    
    // Apply warmth by adjusting R and B channels
    // Positive w increases R (warm), negative increases B (cool)
    float scale = 0.15 * w;  // Reduced scale to prevent overcorrection
    vec3 temp_gain = vec3(1.0 + scale, 1.0, 1.0 - scale);
    
    // Calculate original luminance before applying warmth
    vec3 luma_coeff = vec3(0.2126, 0.7152, 0.0722);
    float orig_luma = dot(c, luma_coeff);
    
    // Apply warmth adjustment
    c = c * temp_gain;
    
    // Preserve luminance by scaling to match original
    float new_luma = dot(c, luma_coeff);
    if (new_luma > 0.001) {
        c *= (orig_luma / new_luma);
    }
    
    return c;
}

// Temperature/Tint adjustment based on industry standards
// Reference: Adobe Camera Raw / Lightroom color temperature model
vec3 temp_tint_adjust(vec3 c, float temp, float tint) {
    if (temp == 0.0 && tint == 0.0) return c;
    
    // BT.709 luminance coefficients
    vec3 luma_coeff = vec3(0.2126, 0.7152, 0.0722);
    float orig_luma = dot(c, luma_coeff);
    
    // Temperature adjustment: Blue <-> Yellow (Orange)
    // Positive temp = warmer (more yellow/orange), negative = cooler (more blue)
    // This shifts the blue-yellow axis
    float temp_scale = 0.3 * temp;
    vec3 temp_gain = vec3(1.0 + temp_scale * 0.8, 1.0, 1.0 - temp_scale);
    
    // Tint adjustment: Green <-> Magenta
    // Positive tint = more magenta, negative = more green
    // This shifts the green-magenta axis
    float tint_scale = 0.2 * tint;
    vec3 tint_gain = vec3(1.0 + tint_scale * 0.5, 1.0 - tint_scale * 0.5, 1.0 + tint_scale * 0.5);
    
    // Apply both adjustments
    c = c * temp_gain * tint_gain;
    
    // Preserve luminance
    float new_luma = dot(c, luma_coeff);
    if (new_luma > 0.001) {
        c *= (orig_luma / new_luma);
    }
    
    return c;
}

void main(){
    vec3 color = texture(uTex, vUV).rgb;
    
    // Apply channel gains first
    color = color * uGain;
    
    // Apply warmth adjustment (for Neutral Gray and Skin Tone modes)
    color = warmth_adjust(color, uWarmth);
    
    // Apply temperature/tint adjustment (for Temp & Tint mode)
    color = temp_tint_adjust(color, uTemperature, uTint);
    
    // Final clamp
    color = clamp(color, 0.0, 1.0);
    FragColor = vec4(color, 1.0);
}
"""


# ======================= Styled Components =======================
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
                outline: 0px;
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

        p1 = QPointF(cx - size, cy - 2)
        p2 = QPointF(cx, cy - 6)
        p3 = QPointF(cx + size, cy - 2)

        p4 = QPointF(cx - size, cy + 2)
        p5 = QPointF(cx, cy + 6)
        p6 = QPointF(cx + size, cy + 2)

        pen = QPen(arrow_color, 2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawPolyline([p1, p2, p3])
        painter.drawPolyline([p4, p5, p6])
        painter.end()


class PipetteButton(QPushButton):
    def __init__(self, icon_path=None, parent=None):
        super().__init__(parent)
        self.icon_path = icon_path
        self.setFixedSize(36, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setStyleSheet("""
            QPushButton {
                background-color: #383838;
                border: 1px solid #555;
                border-radius: 6px;
                color: #ddd;
            }
            QPushButton:hover { background-color: #444; }
            QPushButton:checked { background-color: #4a90e2; border-color: #4a90e2; }
        """)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.icon_path and Path(self.icon_path).exists():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            # Load and draw SVG icon centered
            pixmap = QPixmap(self.icon_path)
            if not pixmap.isNull():
                # Scale to fit button (with padding)
                scaled = pixmap.scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = (self.width() - scaled.width()) // 2
                y = (self.height() - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)


class WarmthSlider(QWidget):
    """Custom slider with gradient background and tick marks"""
    
    # Signal emitted when value changes (during drag or on release)
    valueChanged = Signal(float)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._min = -100.0
        self._max = 100.0
        self._value = 0.0
        self._dragging = False
        self.setFixedHeight(34)
        self.setCursor(Qt.OpenHandCursor)

        self.c_blue_track = QColor(44, 62, 74)
        self.c_orange_track = QColor(74, 62, 32)
        self.c_indicator = QColor(255, 204, 0)
        self.c_tick = QColor(255, 255, 255, 60)

    def _normalised_value(self):
        return (self._value - self._min) / (self._max - self._min)

    def value(self):
        return self._value

    def setValue(self, v):
        self._value = max(self._min, min(self._max, float(v)))
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        # Gradient background
        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gradient.setColorAt(0, self.c_blue_track)
        gradient.setColorAt(1, self.c_orange_track)
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        painter.fillPath(path, gradient)

        # Tick marks
        painter.setPen(QPen(self.c_tick, 1))
        ticks = 50
        for i in range(ticks):
            x = (i / ticks) * rect.width()
            h = 6 if i % 5 == 0 else 3
            painter.drawLine(QPointF(x, 0), QPointF(x, h))

        # Label and value
        font = QFont("Inter", 12, QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(240, 240, 240))
        painter.drawText(rect.adjusted(12, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, "Warmth")
        painter.setPen(QColor(255, 255, 255, 160))
        painter.drawText(rect.adjusted(0, 0, -12, 0), Qt.AlignVCenter | Qt.AlignRight, str(int(self._value)))

        # Position indicator
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
        # Emit signal on release for final update
        self.valueChanged.emit(self._value)

    def _update_from_pos(self, x):
        ratio = max(0, min(1, x / self.width()))
        self._value = self._min + ratio * (self._max - self._min)
        self.valueChanged.emit(self._value)  # Emit during drag
        self.update()


class TemperatureSlider(QWidget):
    """Custom slider for temperature (Kelvin) with blue-orange gradient"""
    
    valueChanged = Signal(float)
    
    # Temperature range in Kelvin
    KELVIN_MIN = 2000.0
    KELVIN_MAX = 10000.0
    KELVIN_DEFAULT = 6500.0  # Daylight
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = self.KELVIN_DEFAULT  # Kelvin temperature
        self._dragging = False
        self.setFixedHeight(34)
        self.setCursor(Qt.OpenHandCursor)

        # Blue (cool) to Orange (warm) gradient colors
        self.c_blue = QColor(50, 100, 180)     # Cool blue
        self.c_orange = QColor(255, 140, 50)   # Warm orange
        self.c_indicator = QColor(255, 255, 255)
        self.c_tick = QColor(255, 255, 255, 60)

    def _normalised_value(self):
        return (self._value - self.KELVIN_MIN) / (self.KELVIN_MAX - self.KELVIN_MIN)

    def value(self):
        return self._value
    
    def kelvinToNormalized(self):
        """Convert Kelvin temperature to normalized value [-1, 1] for shader"""
        # 6500K is neutral (0), lower = cooler (negative), higher = warmer (positive)
        # Map 2000K-10000K to [-1, 1] with 6500K at 0
        range_half = (self.KELVIN_MAX - self.KELVIN_MIN) / 2.0
        center = (self.KELVIN_MAX + self.KELVIN_MIN) / 2.0
        return (self._value - center) / range_half

    def setValue(self, v):
        self._value = max(self.KELVIN_MIN, min(self.KELVIN_MAX, float(v)))
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        # Blue-Orange gradient background
        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gradient.setColorAt(0, self.c_blue)
        gradient.setColorAt(0.5, QColor(180, 180, 180))  # Neutral gray in middle
        gradient.setColorAt(1, self.c_orange)
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        painter.fillPath(path, gradient)

        # Tick marks
        painter.setPen(QPen(self.c_tick, 1))
        ticks = 50
        for i in range(ticks):
            x = (i / ticks) * rect.width()
            h = 6 if i % 5 == 0 else 3
            painter.drawLine(QPointF(x, 0), QPointF(x, h))

        # Label and value
        font = QFont("Inter", 12, QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(240, 240, 240))
        painter.drawText(rect.adjusted(12, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, "Temperature")
        painter.setPen(QColor(255, 255, 255, 160))
        painter.drawText(rect.adjusted(0, 0, -12, 0), Qt.AlignVCenter | Qt.AlignRight, f"{int(self._value)}K")

        # Position indicator
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
        self._value = self.KELVIN_MIN + ratio * (self.KELVIN_MAX - self.KELVIN_MIN)
        self.valueChanged.emit(self._value)
        self.update()


class TintSlider(QWidget):
    """Custom slider for tint with green-magenta gradient"""
    
    valueChanged = Signal(float)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._min = -100.0
        self._max = 100.0
        self._value = 0.0
        self._dragging = False
        self.setFixedHeight(34)
        self.setCursor(Qt.OpenHandCursor)

        # Green to Magenta gradient colors
        self.c_green = QColor(80, 180, 80)     # Green
        self.c_magenta = QColor(200, 80, 180)  # Magenta
        self.c_indicator = QColor(255, 255, 255)
        self.c_tick = QColor(255, 255, 255, 60)

    def _normalised_value(self):
        return (self._value - self._min) / (self._max - self._min)

    def value(self):
        return self._value
    
    def normalizedValue(self):
        """Convert value to normalized [-1, 1] for shader"""
        return self._value / 100.0

    def setValue(self, v):
        self._value = max(self._min, min(self._max, float(v)))
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        # Green-Magenta gradient background
        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gradient.setColorAt(0, self.c_green)
        gradient.setColorAt(0.5, QColor(180, 180, 180))  # Neutral gray in middle
        gradient.setColorAt(1, self.c_magenta)
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        painter.fillPath(path, gradient)

        # Tick marks
        painter.setPen(QPen(self.c_tick, 1))
        ticks = 50
        for i in range(ticks):
            x = (i / ticks) * rect.width()
            h = 6 if i % 5 == 0 else 3
            painter.drawLine(QPointF(x, 0), QPointF(x, h))

        # Label and value
        font = QFont("Inter", 12, QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(240, 240, 240))
        painter.drawText(rect.adjusted(12, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, "Tint")
        painter.setPen(QColor(255, 255, 255, 160))
        val_text = f"+{int(self._value)}" if self._value > 0 else str(int(self._value))
        painter.drawText(rect.adjusted(0, 0, -12, 0), Qt.AlignVCenter | Qt.AlignRight, val_text)

        # Position indicator
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
class GLWBViewer(QOpenGLWidget):
    # Signal emitted when eyedropper picks a color (so UI can update)
    colorPicked = Signal()
    # Signal emitted when temperature/tint values are calculated from eyedropper
    tempTintPicked = Signal(float, float)  # temperature (Kelvin), tint (-100 to 100)
    
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

        self.mode = "Neutral Gray"
        self.gain = np.array([1.0, 1.0, 1.0], dtype=np.float32)
        self.warmth = 0.0
        self.temperature = 0.0  # Normalized temperature [-1, 1]
        self.tint = 0.0         # Normalized tint [-1, 1]

        self._eyedropper_on = False
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
        for n in ["uTex", "uGain", "uWarmth", "uTemperature", "uTint"]:
            self._uniforms[n] = prog.uniformLocation(n)
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
        self.gl.glUniform3f(self._uniforms["uGain"], float(self.gain[0]), float(self.gain[1]), float(self.gain[2]))
        self.gl.glUniform1f(self._uniforms["uWarmth"], float(self.warmth))
        self.gl.glUniform1f(self._uniforms["uTemperature"], float(self.temperature))
        self.gl.glUniform1f(self._uniforms["uTint"], float(self.tint))
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)
        if self._vao: self._vao.release()
        self._shader.release()

    def load_image(self, path: str):
        img = QImage(path)
        if img.isNull():
            return
        self._img = img.convertToFormat(QImage.Format_RGBA8888)
        self._upload_texture()
        self.gain[:] = 1.0
        self.warmth = 0.0
        self.temperature = 0.0
        self.tint = 0.0
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

    def set_mode(self, mode: str):
        self.mode = mode
        self.update()

    def set_warmth(self, v: float):
        self.warmth = v / 100.0  # Convert from -100..100 to -1..1
        self.update()

    def set_temperature(self, v: float):
        """Set temperature from Kelvin value"""
        # Convert Kelvin to normalized value [-1, 1]
        # Map 2000K-10000K to [-1, 1] with center (6000K) at 0
        KELVIN_MIN = 2000.0
        KELVIN_MAX = 10000.0
        range_half = (KELVIN_MAX - KELVIN_MIN) / 2.0
        center = (KELVIN_MAX + KELVIN_MIN) / 2.0
        self.temperature = (v - center) / range_half
        self.update()

    def set_tint(self, v: float):
        """Set tint from -100 to +100"""
        self.tint = v / 100.0  # Convert to [-1, 1]
        self.update()

    def toggle_eyedropper(self, on: bool):
        self._eyedropper_on = on
        self.setCursor(QCursor(Qt.CrossCursor) if on else QCursor(Qt.ArrowCursor))

    def mousePressEvent(self, e):
        if not self._eyedropper_on or self._img is None:
            return super().mousePressEvent(e)

        if e.button() == Qt.LeftButton:
            uvx = max(0.0, min(1.0, e.position().x() / max(1, self.width())))
            uvy = max(0.0, min(1.0, e.position().y() / max(1, self.height())))
            ix = int(uvx * (self._img.width() - 1))
            iy = int(uvy * (self._img.height() - 1))
            rgba = self._img.pixelColor(ix, iy)
            r = rgba.red()   / 255.0
            g = rgba.green() / 255.0
            b = rgba.blue()  / 255.0
            
            # CRITICAL FIX: Reset gain and warmth before applying new pick
            # This prevents accumulation of corrections when clicking multiple times
            self.gain[:] = 1.0
            self.warmth = 0.0
            self.temperature = 0.0
            self.tint = 0.0
            
            self._apply_pick(np.array([r, g, b], dtype=np.float32))
            self.toggle_eyedropper(False)

    def _apply_pick(self, rgb: np.ndarray):
        eps = 1e-6
        rgb = np.clip(rgb, eps, 1.0)

        if self.mode == "Neutral Gray":
            gray = float(rgb.mean())
            gain = np.array([gray / rgb[0], gray / rgb[1], gray / rgb[2]], dtype=np.float32)

        elif self.mode == "Skin Tone":
            # Improved skin tone target based on industry standards
            # ITU-R BT.709 typical skin tone ratios for medium Caucasian/Asian skin in sRGB:
            # R slightly warm (1.13), G neutral (1.00), B slightly cool (0.94)
            # These values align with professional color grading practices
            # Reference: ITU-R BT.709 color space and common skin tone detection algorithms
            target = np.array([1.13, 1.00, 0.94], dtype=np.float32)
            target /= target.mean()
            gain = target / rgb
            # Preserve luminance using BT.709
            w = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
            Y_src = float((w * rgb).sum())
            Y_new = float((w * (rgb * gain)).sum()) + eps
            gain *= (Y_src / Y_new)

        else:  # "Temp & Tint"
            # Calculate temperature and tint from the picked neutral color
            # The idea is to find what correction is needed to neutralize this color
            # Reference: Adobe Camera Raw / Lightroom algorithm approach
            
            gain = np.array([1.0, 1.0, 1.0], dtype=np.float32)
            
            # Calculate temperature offset (blue-yellow axis)
            # If R > B, the area is warm (needs cooling) -> negative temp correction
            # If B > R, the area is cool (needs warming) -> positive temp correction
            r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
            
            # Temperature calculation: based on R/B ratio
            # Neutral point is when R ≈ B
            temp_ratio = r / max(b, eps)
            # Map ratio to temperature offset: ratio > 1 means warm, < 1 means cool
            # We need to apply the OPPOSITE to neutralize
            if temp_ratio > 1.0:
                # Source is warm, apply cooling
                temp_offset = -np.clip((temp_ratio - 1.0) * 0.5, 0, 1)
            else:
                # Source is cool, apply warming
                temp_offset = np.clip((1.0 - temp_ratio) * 0.5, 0, 1)
            
            # Convert to Kelvin: center (6000K) is neutral
            # temp_offset in [-1, 1] maps to [2000K, 10000K]
            KELVIN_MIN = 2000.0
            KELVIN_MAX = 10000.0
            center = (KELVIN_MAX + KELVIN_MIN) / 2.0
            range_half = (KELVIN_MAX - KELVIN_MIN) / 2.0
            kelvin_temp = center + temp_offset * range_half
            kelvin_temp = np.clip(kelvin_temp, KELVIN_MIN, KELVIN_MAX)
            
            # Tint calculation (green-magenta axis)
            # Based on G channel relative to average of R and B
            avg_rb = (r + b) / 2.0
            tint_ratio = g / max(avg_rb, eps)
            # If G > avg(R,B), source is green-tinted, apply magenta
            # If G < avg(R,B), source is magenta-tinted, apply green
            if tint_ratio > 1.0:
                # Source is green, apply magenta (positive tint)
                tint_offset = np.clip((tint_ratio - 1.0) * 100, 0, 100)
            else:
                # Source is magenta, apply green (negative tint)
                tint_offset = -np.clip((1.0 - tint_ratio) * 100, 0, 100)
            
            # Set the calculated values using the same normalization as the sliders
            self.temperature = (kelvin_temp - center) / range_half
            self.tint = tint_offset / 100.0
            
            # Emit signal for UI update
            self.tempTintPicked.emit(kelvin_temp, tint_offset)

        # Normalize gain to prevent exposure changes
        gain /= float(gain.mean() + eps)
        self.gain = gain.astype(np.float32)
        
        # Emit signal to notify UI that a color was picked
        self.colorPicked.emit()
        
        self.update()


# ======================= Main Window =======================
class WBMain(QMainWindow):
    @staticmethod
    def _find_icon_directory():
        """Find the icon directory, trying multiple possible locations"""
        script_path = Path(__file__).resolve()
        
        # Try relative path from demo/white balance/
        possible_paths = [
            script_path.parent.parent.parent / "src" / "iPhoto" / "gui" / "ui" / "icon",
            # Fallback for different directory structures
            script_path.parent.parent.parent / "iPhoto" / "gui" / "ui" / "icon",
        ]
        
        for icon_dir in possible_paths:
            if icon_dir.exists() and icon_dir.is_dir():
                return icon_dir
        
        return None
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("White Balance")
        
        # Set window icon - try multiple possible locations
        icon_dir = self._find_icon_directory()
        wb_icon_path = icon_dir / "whitebalance.square.svg" if icon_dir else None
        if wb_icon_path and wb_icon_path.exists():
            self.setWindowIcon(QIcon(str(wb_icon_path)))
        
        self.resize(1100, 720)
        self.setStyleSheet("background-color: #1a1a1a;")

        self.viewer = GLWBViewer()

        # Control panel with warmth-ui styling
        panel = QWidget()
        panel.setStyleSheet("background-color: #1a1a1a;")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)
        panel_layout.setSpacing(12)

        # Header
        header = QHBoxLayout()
        
        # Title with icon
        title_widget = QWidget()
        title_layout = QHBoxLayout(title_widget)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)
        
        if wb_icon_path and wb_icon_path.exists():
            icon_label = QLabel()
            icon_pixmap = QPixmap(str(wb_icon_path)).scaled(20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            icon_label.setPixmap(icon_pixmap)
            title_layout.addWidget(icon_label)
        
        title_lbl = QLabel("White Balance")
        title_lbl.setStyleSheet("color: #eee; font-weight: bold; font-size: 14px;")
        title_layout.addWidget(title_lbl)
        
        header.addWidget(title_widget)
        header.addStretch()

        open_btn = QPushButton("OPEN")
        open_btn.setFixedSize(50, 20)
        open_btn.setStyleSheet("""
            QPushButton { 
                background-color: #333; color: #999; border: 1px solid #444; 
                border-radius: 10px; font-size: 9px; font-weight: bold;
            }
            QPushButton:hover { background-color: #444; }
        """)
        open_btn.clicked.connect(self.open_image)

        check_btn = QPushButton("✓")
        check_btn.setFixedSize(22, 22)
        check_btn.setStyleSheet("""
            QPushButton {
                background-color: #007aff; 
                border-radius: 11px; 
                color: white; 
                font-weight: bold;
            }
            QPushButton:hover { background-color: #0066dd; }
        """)

        header.addWidget(open_btn)
        header.addWidget(check_btn)

        # Tool row
        tool_row = QHBoxLayout()
        
        eyedropper_icon_path = icon_dir / "eyedropper.svg" if icon_dir else None
        self.pipette = PipetteButton(str(eyedropper_icon_path) if eyedropper_icon_path and eyedropper_icon_path.exists() else None)
        self.pipette.toggled.connect(self.on_eyedropper_toggled)
        
        self.combo = StyledComboBox()
        self.combo.addItems(["Neutral Gray", "Skin Tone", "Temp & Tint"])
        self.combo.currentTextChanged.connect(self.on_mode_changed)

        tool_row.addWidget(self.pipette)
        tool_row.addWidget(self.combo, 1)

        # Warmth slider (for Neutral Gray and Skin Tone modes)
        self.slider = WarmthSlider()
        
        # Temperature slider (for Temp & Tint mode)
        self.temp_slider = TemperatureSlider()
        self.temp_slider.setVisible(False)
        
        # Tint slider (for Temp & Tint mode)
        self.tint_slider = TintSlider()
        self.tint_slider.setVisible(False)

        panel_layout.addLayout(header)
        panel_layout.addLayout(tool_row)
        panel_layout.addWidget(self.slider)
        panel_layout.addWidget(self.temp_slider)
        panel_layout.addWidget(self.tint_slider)

        # Main layout
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.viewer, 1)
        main_layout.addWidget(panel)
        panel.setFixedWidth(320)

        central = QWidget()
        central.setLayout(main_layout)
        self.setCentralWidget(central)

        # Connect slider to warmth updates
        self.slider.valueChanged.connect(lambda v: self.viewer.set_warmth(v))
        
        # Connect temperature and tint sliders
        self.temp_slider.valueChanged.connect(lambda v: self.viewer.set_temperature(v))
        self.tint_slider.valueChanged.connect(lambda v: self.viewer.set_tint(v))
        
        # Connect viewer's colorPicked signal to reset slider
        self.viewer.colorPicked.connect(lambda: self.slider.setValue(0))
        
        # Connect viewer's tempTintPicked signal to update temp/tint sliders
        self.viewer.tempTintPicked.connect(self.on_temp_tint_picked)

    def on_mode_changed(self, text: str):
        self.viewer.set_mode(text)
        self.pipette.setChecked(False)
        
        # Enable eyedropper for all modes (now includes Temp & Tint)
        self.pipette.setEnabled(True)
        
        # Show/hide appropriate sliders
        is_temp_tint = (text == "Temp & Tint")
        self.slider.setVisible(not is_temp_tint)
        self.temp_slider.setVisible(is_temp_tint)
        self.tint_slider.setVisible(is_temp_tint)
        
        # Reset values when switching modes
        if is_temp_tint:
            self.temp_slider.setValue(TemperatureSlider.KELVIN_DEFAULT)
            self.tint_slider.setValue(0)
            self.viewer.warmth = 0.0
            self.viewer.temperature = 0.0
            self.viewer.tint = 0.0
        else:
            self.slider.setValue(0)
            self.viewer.temperature = 0.0
            self.viewer.tint = 0.0
        
        self.viewer.gain[:] = 1.0
        self.viewer.update()

    def on_temp_tint_picked(self, temp_kelvin: float, tint_value: float):
        """Handle temperature/tint values from eyedropper"""
        self.temp_slider.setValue(temp_kelvin)
        self.tint_slider.setValue(tint_value)

    def on_eyedropper_toggled(self, checked: bool):
        self.viewer.toggle_eyedropper(checked)

    def open_image(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if fn:
            self.viewer.load_image(fn)
            # Reset all sliders
            self.slider.setValue(0)
            self.temp_slider.setValue(TemperatureSlider.KELVIN_DEFAULT)
            self.tint_slider.setValue(0)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Segoe UI", 9)
    app.setFont(font)
    win = WBMain()
    win.show()
    sys.exit(app.exec())
