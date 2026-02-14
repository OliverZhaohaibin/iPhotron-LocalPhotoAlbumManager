# -*- coding: utf-8 -*-
"""Shader compilation and program management for the GL renderer."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject
from PySide6.QtOpenGL import (
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)
from OpenGL import GL as gl

_LOGGER = logging.getLogger(__name__)


_OVERLAY_VERTEX_SHADER = """
#version 330 core
layout(location = 0) in vec2 aPos;
void main() {
    gl_Position = vec4(aPos, 0.0, 1.0);
}
"""


_OVERLAY_FRAGMENT_SHADER = """
#version 330 core
out vec4 FragColor;
uniform vec4 uColor;
void main() {
    FragColor = uColor;
}
"""


_UNIFORM_NAMES = (
    "uTex",
    "uBrilliance",
    "uExposure",
    "uHighlights",
    "uShadows",
    "uBrightness",
    "uContrast",
    "uBlackPoint",
    "uSaturation",
    "uVibrance",
    "uColorCast",
    "uGain",
    "uBWParams",
    "uBWEnabled",
    "uCurveLUT",
    "uCurveEnabled",
    "uLevelsLUT",
    "uLevelsEnabled",
    "uWBWarmth",
    "uWBTemperature",
    "uWBTint",
    "uWBEnabled",
    "uTime",
    "uViewSize",
    "uTexSize",
    "uScale",
    "uPan",
    "uImgScale",
    "uImgOffset",
    "uCropCX",
    "uCropCY",
    "uCropW",
    "uCropH",
    "uPerspectiveMatrix",
    "uRotate90",
    "uSCRange0",
    "uSCRange1",
    "uSCEnabled",
)


def _load_shader_source(filename: str) -> str:
    """Return the GLSL source stored alongside this module."""

    shader_path = Path(__file__).resolve().with_name(filename)
    try:
        return shader_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Failed to load shader '{filename}': {exc}") from exc


class ShaderManager:
    """Owns the main and overlay shader programs, VAOs, and uniform location cache."""

    def __init__(
        self,
        gl_funcs,
        *,
        parent: Optional[QObject] = None,
    ) -> None:
        self._gl_funcs = gl_funcs
        self._parent = parent
        self.program: Optional[QOpenGLShaderProgram] = None
        self.dummy_vao: Optional[QOpenGLVertexArrayObject] = None
        self.uniform_locations: dict[str, int] = {}
        self.overlay_program: Optional[QOpenGLShaderProgram] = None
        self.overlay_vao: Optional[QOpenGLVertexArrayObject] = None
        self.overlay_vbo: int = 0

    def initialize(self) -> None:
        """Compile shaders, create VAOs, and cache uniform locations."""

        self.destroy()

        program = QOpenGLShaderProgram(self._parent)
        vert_source = _load_shader_source("gl_image_viewer.vert")
        frag_source = _load_shader_source("gl_image_viewer.frag")
        if not program.addShaderFromSourceCode(QOpenGLShader.Vertex, vert_source):
            message = program.log()
            _LOGGER.error("Vertex shader compilation failed: %s", message)
            raise RuntimeError("Unable to compile vertex shader")
        if not program.addShaderFromSourceCode(QOpenGLShader.Fragment, frag_source):
            message = program.log()
            _LOGGER.error("Fragment shader compilation failed: %s", message)
            raise RuntimeError("Unable to compile fragment shader")
        if not program.link():
            message = program.log()
            _LOGGER.error("Shader program link failed: %s", message)
            raise RuntimeError("Unable to link shader program")

        self.program = program

        vao = QOpenGLVertexArrayObject(self._parent)
        vao.create()
        self.dummy_vao = vao if vao.isCreated() else None

        gf = self._gl_funcs
        gf.glDisable(gl.GL_DEPTH_TEST)
        gf.glDisable(gl.GL_CULL_FACE)
        gf.glDisable(gl.GL_BLEND)

        program.bind()
        try:
            for name in _UNIFORM_NAMES:
                self.uniform_locations[name] = program.uniformLocation(name)
        finally:
            program.release()

        # Overlay program
        overlay_prog = QOpenGLShaderProgram(self._parent)
        if not overlay_prog.addShaderFromSourceCode(
            QOpenGLShader.Vertex, _OVERLAY_VERTEX_SHADER
        ):
            raise RuntimeError("Unable to compile overlay vertex shader")
        if not overlay_prog.addShaderFromSourceCode(
            QOpenGLShader.Fragment, _OVERLAY_FRAGMENT_SHADER
        ):
            raise RuntimeError("Unable to compile overlay fragment shader")
        if not overlay_prog.link():
            raise RuntimeError("Unable to link overlay shader program")
        self.overlay_program = overlay_prog

        overlay_vao = QOpenGLVertexArrayObject(self._parent)
        overlay_vao.create()
        self.overlay_vao = overlay_vao if overlay_vao.isCreated() else None
        buffer_id = gl.glGenBuffers(1)
        if isinstance(buffer_id, (tuple, list)):
            buffer_id = buffer_id[0]
        self.overlay_vbo = int(buffer_id)

    def destroy(self) -> None:
        """Release shader programs, VAOs, and the overlay VBO."""

        if self.dummy_vao is not None:
            self.dummy_vao.destroy()
            self.dummy_vao = None
        if self.program is not None:
            self.program.removeAllShaders()
            self.program = None
        self.uniform_locations.clear()
        if self.overlay_vao is not None:
            self.overlay_vao.destroy()
            self.overlay_vao = None
        if self.overlay_program is not None:
            self.overlay_program.removeAllShaders()
            self.overlay_program = None
        if self.overlay_vbo:
            gl.glDeleteBuffers(1, np.array([int(self.overlay_vbo)], dtype=np.uint32))
            self.overlay_vbo = 0
