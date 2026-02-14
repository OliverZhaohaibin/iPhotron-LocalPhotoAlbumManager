# -*- coding: utf-8 -*-
"""OpenGL renderer used by :class:`GLImageViewer`.

This module isolates all raw OpenGL calls so the widget itself can focus on
state orchestration and Qt event handling.  The renderer loads the GLSL shader
pair, owns the GPU resources (VAO, shader program, texture) and exposes a small
API tailored to the viewer.

Implementation is split across helper modules:

* :mod:`gl_shader_manager` – shader compilation and program lifecycle
* :mod:`gl_texture_manager` – GPU texture upload / deletion
* :mod:`gl_uniform_state` – uniform setter convenience wrappers
* :mod:`gl_offscreen` – off-screen FBO rendering
"""

from __future__ import annotations

import logging
import math
from typing import Mapping, Optional

import numpy as np
from PySide6.QtCore import QObject, QPointF, QSize
from PySide6.QtGui import QImage
from PySide6.QtOpenGL import (
    QOpenGLFunctions_3_3_Core,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)
from OpenGL import GL as gl
from shiboken6.Shiboken import VoidPtr

from ....core.selective_color_resolver import NUM_RANGES, SAT_GATE_LO, SAT_GATE_HI

from .perspective_math import build_perspective_matrix
from .gl_shader_manager import (
    ShaderManager,
    _load_shader_source,
    _OVERLAY_VERTEX_SHADER,
    _OVERLAY_FRAGMENT_SHADER,
)
from .gl_texture_manager import TextureManager
from .gl_uniform_state import UniformState
from .gl_offscreen import render_offscreen_image as _render_offscreen_image

_LOGGER = logging.getLogger(__name__)


class GLRenderer:
    """Encapsulates the OpenGL drawing routine for the viewer texture."""

    def __init__(
        self,
        gl_funcs: QOpenGLFunctions_3_3_Core,
        *,
        parent: Optional[QObject] = None,
    ) -> None:
        self._gl_funcs = gl_funcs
        self._parent = parent

        self._shader_mgr = ShaderManager(gl_funcs, parent=parent)
        self._tex_mgr = TextureManager()
        # UniformState shares the same dict instance populated by ShaderManager
        self._uniform = UniformState(gl_funcs, self._shader_mgr.uniform_locations)

    # ------------------------------------------------------------------
    # Backward-compatible attribute access  (used by tests & internals)
    # ------------------------------------------------------------------
    @property
    def _program(self):
        return self._shader_mgr.program

    @_program.setter
    def _program(self, value):
        self._shader_mgr.program = value

    @property
    def _dummy_vao(self):
        return self._shader_mgr.dummy_vao

    @property
    def _uniform_locations(self):
        return self._shader_mgr.uniform_locations

    @property
    def _overlay_program(self):
        return self._shader_mgr.overlay_program

    @property
    def _overlay_vao(self):
        return self._shader_mgr.overlay_vao

    @property
    def _overlay_vbo(self):
        return self._shader_mgr.overlay_vbo

    @property
    def _texture_id(self):
        return self._tex_mgr._texture_id

    @_texture_id.setter
    def _texture_id(self, value):
        self._tex_mgr._texture_id = value

    @property
    def _texture_width(self):
        return self._tex_mgr._texture_width

    @_texture_width.setter
    def _texture_width(self, value):
        self._tex_mgr._texture_width = value

    @property
    def _texture_height(self):
        return self._tex_mgr._texture_height

    @_texture_height.setter
    def _texture_height(self, value):
        self._tex_mgr._texture_height = value

    @property
    def _curve_lut_texture_id(self):
        return self._tex_mgr._curve_lut_texture_id

    @property
    def _levels_lut_texture_id(self):
        return self._tex_mgr._levels_lut_texture_id

    # ------------------------------------------------------------------
    # Resource management
    # ------------------------------------------------------------------
    def initialize_resources(self) -> None:
        """Compile the shader program and set up immutable GL state."""

        self.destroy_resources()
        self._shader_mgr.initialize()

    def destroy_resources(self) -> None:
        """Release the shader program, VAO and resident texture."""

        self._tex_mgr.destroy()
        self._shader_mgr.destroy()

    # ------------------------------------------------------------------
    # Texture management  (delegates to TextureManager)
    # ------------------------------------------------------------------
    def upload_texture(self, image: QImage) -> tuple[int, int, int]:
        """Upload *image* to the GPU and return ``(id, width, height)``."""
        return self._tex_mgr.upload_texture(image)

    def delete_texture(self) -> None:
        """Delete the currently bound texture, if any."""
        self._tex_mgr.delete_texture()

    def _delete_curve_lut_texture(self) -> None:
        self._tex_mgr._delete_curve_lut_texture()

    def upload_curve_lut(self, lut_data: np.ndarray) -> None:
        """Upload a 256×3 float32 curve LUT to the GPU."""
        self._tex_mgr.upload_curve_lut(lut_data)

    def _delete_levels_lut_texture(self) -> None:
        self._tex_mgr._delete_levels_lut_texture()

    def upload_levels_lut(self, lut_data: np.ndarray) -> None:
        """Upload a 256×3 float32 levels LUT to the GPU."""
        self._tex_mgr.upload_levels_lut(lut_data)

    def has_texture(self) -> bool:
        """Return ``True`` if a GPU texture is currently resident."""
        return self._tex_mgr.has_texture()

    def texture_size(self) -> tuple[int, int]:
        """Return the uploaded texture dimensions as ``(width, height)``."""
        return self._tex_mgr.texture_size()

    # ------------------------------------------------------------------
    # Uniform helpers  (delegates to UniformState)
    # ------------------------------------------------------------------
    def _set_uniform1i(self, name: str, value: int) -> None:
        self._uniform._set_uniform1i(name, value)

    def _set_uniform1f(self, name: str, value: float) -> None:
        self._uniform._set_uniform1f(name, value)

    def _set_uniform2f(self, name: str, x: float, y: float) -> None:
        self._uniform._set_uniform2f(name, x, y)

    def _set_uniform3f(self, name: str, x: float, y: float, z: float) -> None:
        self._uniform._set_uniform3f(name, x, y, z)

    def _set_uniform4f(self, name: str, x: float, y: float, z: float, w: float) -> None:
        self._uniform._set_uniform4f(name, x, y, z, w)

    def _set_uniform_matrix3(self, name: str, matrix: np.ndarray) -> None:
        self._uniform._set_uniform_matrix3(name, matrix)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def render(
        self,
        *,
        view_width: float,
        view_height: float,
        scale: float,
        pan: QPointF,
        adjustments: Mapping[str, float],
        time_value: float | None = None,
        img_scale: float = 1.0,
        img_offset: Optional[QPointF] = None,
        logical_tex_size: tuple[float, float] | None = None,
    ) -> None:
        """Draw the textured triangle covering the current viewport."""

        if self._program is None:
            raise RuntimeError("Renderer has not been initialised")
        if self._texture_id == 0:
            return
        if scale <= 0.0:
            return

        gf = self._gl_funcs
        if not self._program.bind():
            _LOGGER.error("Failed to bind shader program: %s", self._program.log())
            return

        try:
            if self._dummy_vao is not None:
                self._dummy_vao.bind()

            offset_value = img_offset or QPointF(0.0, 0.0)

            gf.glActiveTexture(gl.GL_TEXTURE0)
            gf.glBindTexture(gl.GL_TEXTURE_2D, int(self._texture_id))
            self._set_uniform1i("uTex", 0)

            def adjustment_value(key: str, default: float = 0.0) -> float:
                return float(adjustments.get(key, default))

            self._set_uniform1f("uBrilliance", adjustment_value("Brilliance"))
            self._set_uniform1f("uExposure", adjustment_value("Exposure"))
            self._set_uniform1f("uHighlights", adjustment_value("Highlights"))
            self._set_uniform1f("uShadows", adjustment_value("Shadows"))
            self._set_uniform1f("uBrightness", adjustment_value("Brightness"))
            self._set_uniform1f("uContrast", adjustment_value("Contrast"))
            self._set_uniform1f("uBlackPoint", adjustment_value("BlackPoint"))
            self._set_uniform1f("uSaturation", adjustment_value("Saturation"))
            self._set_uniform1f("uVibrance", adjustment_value("Vibrance"))
            self._set_uniform1f("uColorCast", adjustment_value("Cast"))
            self._set_uniform3f(
                "uGain",
                float(adjustments.get("Color_Gain_R", 1.0)),
                float(adjustments.get("Color_Gain_G", 1.0)),
                float(adjustments.get("Color_Gain_B", 1.0)),
            )
            self._set_uniform4f(
                "uBWParams",
                adjustment_value("BWIntensity"),
                adjustment_value("BWNeutrals"),
                adjustment_value("BWTone"),
                adjustment_value("BWGrain"),
            )
            bw_enabled_value = adjustments.get("BW_Enabled", adjustments.get("BWEnabled", 0.0))
            self._set_uniform1i("uBWEnabled", 1 if bool(bw_enabled_value) else 0)

            # White Balance uniforms
            wb_enabled_value = adjustments.get("WB_Enabled", adjustments.get("WBEnabled", 0.0))
            self._set_uniform1i("uWBEnabled", 1 if bool(wb_enabled_value) else 0)
            self._set_uniform1f("uWBWarmth", adjustment_value("WBWarmth"))
            self._set_uniform1f("uWBTemperature", adjustment_value("WBTemperature"))
            self._set_uniform1f("uWBTint", adjustment_value("WBTint"))

            # Curve LUT texture binding
            curve_enabled_value = adjustments.get("Curve_Enabled", False)
            has_curve_lut_texture = bool(self._curve_lut_texture_id)
            effective_curve_enabled = bool(curve_enabled_value) and has_curve_lut_texture
            self._set_uniform1i("uCurveEnabled", 1 if effective_curve_enabled else 0)
            if has_curve_lut_texture:
                gf.glActiveTexture(gl.GL_TEXTURE1)
                gf.glBindTexture(gl.GL_TEXTURE_2D, int(self._curve_lut_texture_id))
                self._set_uniform1i("uCurveLUT", 1)
            else:
                self._set_uniform1i("uCurveLUT", 0)

            # Levels LUT texture binding
            levels_enabled_value = adjustments.get("Levels_Enabled", False)
            has_levels_lut_texture = bool(self._levels_lut_texture_id)
            effective_levels_enabled = bool(levels_enabled_value) and has_levels_lut_texture
            self._set_uniform1i("uLevelsEnabled", 1 if effective_levels_enabled else 0)
            if has_levels_lut_texture:
                gf.glActiveTexture(gl.GL_TEXTURE2)
                gf.glBindTexture(gl.GL_TEXTURE_2D, int(self._levels_lut_texture_id))
                self._set_uniform1i("uLevelsLUT", 2)
            else:
                self._set_uniform1i("uLevelsLUT", 0)

            # Selective Color uniforms
            sc_enabled_value = adjustments.get("SelectiveColor_Enabled", False)
            self._set_uniform1i("uSCEnabled", 1 if bool(sc_enabled_value) else 0)
            sc_ranges = adjustments.get("SelectiveColor_Ranges")
            if isinstance(sc_ranges, list) and len(sc_ranges) == NUM_RANGES:
                u0 = np.zeros((NUM_RANGES, 4), dtype=np.float32)
                u1 = np.zeros((NUM_RANGES, 4), dtype=np.float32)
                for idx, rng in enumerate(sc_ranges):
                    if isinstance(rng, (list, tuple)) and len(rng) >= 5:
                        center = float(rng[0])
                        range_slider = float(np.clip(rng[1], 0.0, 1.0))
                        deg = 5.0 + (70.0 - 5.0) * range_slider
                        width_hue = float(np.clip(deg / 360.0, 0.001, 0.5))
                        u0[idx] = [center, width_hue, float(rng[2]), float(rng[3])]
                        u1[idx] = [float(rng[4]), SAT_GATE_LO, SAT_GATE_HI, 1.0]
                loc0 = self._uniform_locations.get("uSCRange0", -1)
                loc1 = self._uniform_locations.get("uSCRange1", -1)
                if loc0 != -1:
                    gl.glUniform4fv(loc0, NUM_RANGES, u0)
                if loc1 != -1:
                    gl.glUniform4fv(loc1, NUM_RANGES, u1)

            if time_value is not None:
                self._set_uniform1f("uTime", time_value)

            safe_scale = max(scale, 1e-6)
            safe_img_scale = max(img_scale, 1e-6)
            self._set_uniform1f("uScale", safe_scale)
            self._set_uniform2f("uViewSize", max(view_width, 1.0), max(view_height, 1.0))

            # CRITICAL: uTexSize must match ViewTransformController's coordinate space.
            logical_w: float
            logical_h: float
            if logical_tex_size is None:
                rotate_steps_val = int(float(adjustments.get("Crop_Rotate90", 0.0))) % 4
                if rotate_steps_val % 2 == 1:
                    logical_w = float(self._texture_height)
                    logical_h = float(self._texture_width)
                else:
                    logical_w = float(self._texture_width)
                    logical_h = float(self._texture_height)
            else:
                logical_w, logical_h = logical_tex_size

            safe_logical_w = float(max(1.0, logical_w))
            safe_logical_h = float(max(1.0, logical_h))
            self._set_uniform2f("uTexSize", safe_logical_w, safe_logical_h)

            self._set_uniform2f("uPan", float(pan.x()), float(pan.y()))
            self._set_uniform1f("uImgScale", safe_img_scale)
            self._set_uniform2f(
                "uImgOffset",
                float(offset_value.x()),
                float(offset_value.y()),
            )

            # Pass crop parameters to shader
            self._set_uniform1f("uCropCX", adjustment_value("Crop_CX", 0.5))
            self._set_uniform1f("uCropCY", adjustment_value("Crop_CY", 0.5))
            self._set_uniform1f("uCropW", adjustment_value("Crop_W", 1.0))
            self._set_uniform1f("uCropH", adjustment_value("Crop_H", 1.0))
            straighten_value = adjustment_value("Crop_Straighten", 0.0)
            rotate_steps = int(float(adjustments.get("Crop_Rotate90", 0.0)))
            flip_enabled = bool(adjustments.get("Crop_FlipH", False))

            self._set_uniform1i("uRotate90", rotate_steps % 4)

            logical_aspect_ratio = logical_w / logical_h
            if not math.isfinite(logical_aspect_ratio) or logical_aspect_ratio <= 1e-6:
                logical_aspect_ratio = 1.0

            perspective_matrix = build_perspective_matrix(
                adjustment_value("Perspective_Vertical", 0.0),
                adjustment_value("Perspective_Horizontal", 0.0),
                image_aspect_ratio=logical_aspect_ratio,
                straighten_degrees=straighten_value,
                rotate_steps=0,
                flip_horizontal=flip_enabled,
            )
            self._set_uniform_matrix3("uPerspectiveMatrix", perspective_matrix)

            gf.glDrawArrays(gl.GL_TRIANGLES, 0, 3)
        finally:
            if self._dummy_vao is not None:
                self._dummy_vao.release()
            self._program.release()

        error = gf.glGetError()
        if error != gl.GL_NO_ERROR:
            _LOGGER.warning("OpenGL error after draw: 0x%04X", int(error))

    def draw_crop_overlay(
        self,
        *,
        view_width: float,
        view_height: float,
        crop_rect: Mapping[str, float],
        faded: bool = False,
    ) -> None:
        """Render the semi-transparent crop mask and interactive handles."""

        if (
            self._overlay_program is None
            or self._overlay_vao is None
            or self._overlay_vbo == 0
        ):
            return

        vw = max(1.0, float(view_width))
        vh = max(1.0, float(view_height))

        left = float(crop_rect.get("left", 0.0))
        right = float(crop_rect.get("right", vw))
        top = float(crop_rect.get("top", 0.0))
        bottom = float(crop_rect.get("bottom", vh))

        program = self._overlay_program
        vao = self._overlay_vao
        gf = self._gl_funcs

        alpha = 1.0 if faded else 0.55
        overlay_colour = (0.0, 0.0, 0.0, alpha)
        border_colour = (1.0, 0.85, 0.2, 1.0)

        def _viewport_rect_to_clip(
            rect: tuple[float, float, float, float]
        ) -> np.ndarray:
            """Convert a viewport-space rectangle into interleaved clip coordinates."""

            left_px, top_px, right_px, bottom_px = rect
            points = [
                (left_px, top_px),
                (right_px, top_px),
                (right_px, bottom_px),
                (left_px, bottom_px),
            ]
            coords: list[float] = []
            for px, py in points:
                x_ndc = (2.0 * px / vw) - 1.0
                y_ndc = 1.0 - (2.0 * py / vh)
                coords.extend((x_ndc, y_ndc))
            return np.array(coords, dtype=np.float32)

        def _draw(
            vertices: np.ndarray, mode: int, colour: tuple[float, float, float, float]
        ) -> None:
            """Upload *vertices* and issue a draw call with the provided colour."""

            program.setUniformValue("uColor", *colour)
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, int(self._overlay_vbo))
            gl.glBufferData(gl.GL_ARRAY_BUFFER, vertices.nbytes, vertices, gl.GL_DYNAMIC_DRAW)
            gf.glEnableVertexAttribArray(0)
            gf.glVertexAttribPointer(0, 2, gl.GL_FLOAT, False, 0, VoidPtr(0))
            gf.glDrawArrays(mode, 0, int(vertices.size // 2))
            gf.glDisableVertexAttribArray(0)
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)

        gf.glEnable(gl.GL_BLEND)
        gf.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gf.glColorMask(True, True, True, False)

        if not program.bind():
            gf.glColorMask(True, True, True, True)
            gf.glDisable(gl.GL_BLEND)
            return

        try:
            vao.bind()

            quads = [
                (0.0, 0.0, vw, top),
                (0.0, bottom, vw, vh),
                (0.0, top, left, bottom),
                (right, top, vw, bottom),
            ]
            for quad in quads:
                vertices = _viewport_rect_to_clip(quad)
                _draw(vertices, gl.GL_TRIANGLE_FAN, overlay_colour)

            if not faded:
                border_vertices = _viewport_rect_to_clip((left, top, right, bottom))
                _draw(border_vertices, gl.GL_LINE_LOOP, border_colour)

                handle_size = 7.0
                corner_positions = [
                    (left, top),
                    (right, top),
                    (right, bottom),
                    (left, bottom),
                ]
                for cx, cy in corner_positions:
                    square = (
                        cx - handle_size,
                        cy - handle_size,
                        cx + handle_size,
                        cy + handle_size,
                    )
                    vertices = _viewport_rect_to_clip(square)
                    _draw(vertices, gl.GL_TRIANGLE_FAN, border_colour)

                edge_half_length = 16.0
                edge_half_thickness = 3.0
                horizontal_edges = [
                    ((left + right) * 0.5, top),
                    ((left + right) * 0.5, bottom),
                ]
                vertical_edges = [
                    (left, (top + bottom) * 0.5),
                    (right, (top + bottom) * 0.5),
                ]
                for cx, cy in horizontal_edges:
                    rect = (
                        cx - edge_half_length,
                        cy - edge_half_thickness,
                        cx + edge_half_length,
                        cy + edge_half_thickness,
                    )
                    vertices = _viewport_rect_to_clip(rect)
                    _draw(vertices, gl.GL_TRIANGLE_FAN, border_colour)
                for cx, cy in vertical_edges:
                    rect = (
                        cx - edge_half_thickness,
                        cy - edge_half_length,
                        cx + edge_half_thickness,
                        cy + edge_half_length,
                    )
                    vertices = _viewport_rect_to_clip(rect)
                    _draw(vertices, gl.GL_TRIANGLE_FAN, border_colour)
        finally:
            vao.release()
            program.release()
            gf.glColorMask(True, True, True, True)
            gf.glDisable(gl.GL_BLEND)

    # ------------------------------------------------------------------
    # Off-screen rendering  (delegates to gl_offscreen module)
    # ------------------------------------------------------------------
    def render_offscreen_image(
        self,
        image: QImage,
        adjustments: Mapping[str, float],
        target_size: QSize,
        time_base: float = 0.0,
    ) -> QImage:
        """Render the image into an off-screen framebuffer.

        Parameters
        ----------
        image:
            Source image to render.
        adjustments:
            Mapping of shader uniform values to apply during rendering.
        target_size:
            Final size of the rendered preview. The method clamps the width
            and height to at least one pixel to avoid driver errors.
        time_base:
            Time base for animated effects (default: 0.0).

        Returns
        -------
        QImage
            CPU-side image containing the rendered frame, converted to Format_ARGB32.
        """
        return _render_offscreen_image(self, image, adjustments, target_size, time_base)
