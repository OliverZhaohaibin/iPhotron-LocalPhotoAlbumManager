# -*- coding: utf-8 -*-
"""GPU texture management for the GL renderer."""

from __future__ import annotations

import logging

import numpy as np
from PySide6.QtGui import QImage
from OpenGL import GL as gl

_LOGGER = logging.getLogger(__name__)


class TextureManager:
    """Manages the main image texture and auxiliary LUT textures."""

    def __init__(self) -> None:
        self._texture_id: int = 0
        self._texture_width: int = 0
        self._texture_height: int = 0
        self._curve_lut_texture_id: int = 0
        self._levels_lut_texture_id: int = 0

    # ------------------------------------------------------------------
    # Main texture
    # ------------------------------------------------------------------
    def upload_texture(self, image: QImage) -> tuple[int, int, int]:
        """Upload *image* to the GPU and return ``(id, width, height)``."""

        if image.isNull():
            raise ValueError("Cannot upload a null QImage")

        qimage = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = qimage.width(), qimage.height()
        buffer = qimage.constBits()
        byte_count = qimage.sizeInBytes()
        if hasattr(buffer, "setsize"):
            buffer.setsize(byte_count)
        else:
            buffer = buffer[:byte_count]

        if self._texture_id:
            gl.glDeleteTextures([int(self._texture_id)])
            self._texture_id = 0

        tex_id = gl.glGenTextures(1)
        if isinstance(tex_id, (tuple, list)):
            tex_id = tex_id[0]
        self._texture_id = int(tex_id)
        self._texture_width = int(width)
        self._texture_height = int(height)

        gl.glBindTexture(gl.GL_TEXTURE_2D, self._texture_id)
        gl.glTexImage2D(
            gl.GL_TEXTURE_2D,
            0,
            gl.GL_RGBA8,
            width,
            height,
            0,
            gl.GL_RGBA,
            gl.GL_UNSIGNED_BYTE,
            None,
        )
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        row_length = qimage.bytesPerLine() // 4
        gl.glPixelStorei(gl.GL_UNPACK_ROW_LENGTH, row_length)
        gl.glTexSubImage2D(
            gl.GL_TEXTURE_2D,
            0,
            0,
            0,
            width,
            height,
            gl.GL_RGBA,
            gl.GL_UNSIGNED_BYTE,
            buffer,
        )
        gl.glPixelStorei(gl.GL_UNPACK_ROW_LENGTH, 0)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 4)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)

        error = gl.glGetError()
        if error != gl.GL_NO_ERROR:
            _LOGGER.warning("OpenGL error after texture upload: 0x%04X", int(error))

        return self._texture_id, self._texture_width, self._texture_height

    def delete_texture(self) -> None:
        """Delete the currently bound texture, if any."""

        if not self._texture_id:
            return
        gl.glDeleteTextures(1, np.array([int(self._texture_id)], dtype=np.uint32))
        self._texture_id = 0
        self._texture_width = 0
        self._texture_height = 0

    # ------------------------------------------------------------------
    # Curve LUT texture
    # ------------------------------------------------------------------
    def _delete_curve_lut_texture(self) -> None:
        """Delete the curve LUT texture, if any."""

        if not self._curve_lut_texture_id:
            return
        gl.glDeleteTextures(1, np.array([int(self._curve_lut_texture_id)], dtype=np.uint32))
        self._curve_lut_texture_id = 0

    def upload_curve_lut(self, lut_data: np.ndarray) -> None:
        """Upload a 256x3 float32 LUT to the GPU as a 256x1 RGB texture.

        Args:
            lut_data: numpy array of shape (256, 3) with float32 values in [0, 1]
        """
        if lut_data is None or lut_data.shape != (256, 3):
            return

        lut_data = np.ascontiguousarray(lut_data, dtype=np.float32)

        if self._curve_lut_texture_id:
            gl.glDeleteTextures(1, np.array([int(self._curve_lut_texture_id)], dtype=np.uint32))
            self._curve_lut_texture_id = 0

        tex_id = gl.glGenTextures(1)
        if isinstance(tex_id, (tuple, list)):
            tex_id = tex_id[0]
        self._curve_lut_texture_id = int(tex_id)

        gl.glBindTexture(gl.GL_TEXTURE_2D, self._curve_lut_texture_id)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)

        gl.glTexImage2D(
            gl.GL_TEXTURE_2D,
            0,
            gl.GL_RGB32F,
            256,
            1,
            0,
            gl.GL_RGB,
            gl.GL_FLOAT,
            lut_data,
        )
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)

        error = gl.glGetError()
        if error != gl.GL_NO_ERROR:
            _LOGGER.warning("OpenGL error after curve LUT upload: 0x%04X", int(error))
            self._delete_curve_lut_texture()
            return

    # ------------------------------------------------------------------
    # Levels LUT texture
    # ------------------------------------------------------------------
    def _delete_levels_lut_texture(self) -> None:
        """Delete the levels LUT texture, if any."""

        if not self._levels_lut_texture_id:
            return
        gl.glDeleteTextures(1, np.array([int(self._levels_lut_texture_id)], dtype=np.uint32))
        self._levels_lut_texture_id = 0

    def upload_levels_lut(self, lut_data: np.ndarray) -> None:
        """Upload a 256x3 float32 levels LUT to the GPU as a 256x1 RGB texture.

        Args:
            lut_data: numpy array of shape (256, 3) with float32 values in [0, 1]
        """
        if lut_data is None or lut_data.shape != (256, 3):
            return

        lut_data = np.ascontiguousarray(lut_data, dtype=np.float32)

        if self._levels_lut_texture_id:
            gl.glDeleteTextures(1, np.array([int(self._levels_lut_texture_id)], dtype=np.uint32))
            self._levels_lut_texture_id = 0

        tex_id = gl.glGenTextures(1)
        if isinstance(tex_id, (tuple, list)):
            tex_id = tex_id[0]
        self._levels_lut_texture_id = int(tex_id)

        gl.glBindTexture(gl.GL_TEXTURE_2D, self._levels_lut_texture_id)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)

        gl.glTexImage2D(
            gl.GL_TEXTURE_2D,
            0,
            gl.GL_RGB32F,
            256,
            1,
            0,
            gl.GL_RGB,
            gl.GL_FLOAT,
            lut_data,
        )
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)

        error = gl.glGetError()
        if error != gl.GL_NO_ERROR:
            _LOGGER.warning("OpenGL error after levels LUT upload: 0x%04X", int(error))
            self._delete_levels_lut_texture()
            return

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def has_texture(self) -> bool:
        """Return ``True`` if a GPU texture is currently resident."""

        return self._texture_id != 0

    def texture_size(self) -> tuple[int, int]:
        """Return the uploaded texture dimensions as ``(width, height)``."""

        return self._texture_width, self._texture_height

    def destroy(self) -> None:
        """Delete all managed textures."""

        self.delete_texture()
        self._delete_curve_lut_texture()
        self._delete_levels_lut_texture()
