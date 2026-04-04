# -*- coding: utf-8 -*-
"""GPU texture management for the GL renderer."""

from __future__ import annotations

import logging
import sys

import numpy as np
from OpenGL import GL as gl
from PySide6.QtGui import QImage

try:
    from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat
except (ModuleNotFoundError, ImportError):  # pragma: no cover
    QVideoFrame = None  # type: ignore[assignment, misc]
    QVideoFrameFormat = None  # type: ignore[assignment, misc]

_LOGGER = logging.getLogger(__name__)

_VIDEO_FMT_NONE = 0
_VIDEO_FMT_NV12 = 1
_VIDEO_FMT_P010 = 2

_CS_BT601 = 0
_CS_BT709 = 1
_CS_BT2020 = 2

_TF_SDR = 0
_TF_PQ = 1
_TF_HLG = 2

_RANGE_LIMITED = 0
_RANGE_FULL = 1


def _packed_frame_upload_spec(
    fmt: "QVideoFrameFormat | None",
) -> tuple[int, int, int] | None:
    """Return ``(gl_format, gl_type, bytes_per_pixel)`` for packed RGB frames."""

    if fmt is None or QVideoFrameFormat is None:
        return None

    pf = fmt.pixelFormat()
    pixel_enum = QVideoFrameFormat.PixelFormat
    candidates = (
        ("Format_RGBA8888", gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, 4),
        ("Format_BGRA8888", gl.GL_BGRA, gl.GL_UNSIGNED_BYTE, 4),
        ("Format_RGBX8888", gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, 4),
        ("Format_BGRX8888", gl.GL_BGRA, gl.GL_UNSIGNED_BYTE, 4),
    )
    for name, pixel_format, pixel_type, bytes_per_pixel in candidates:
        enum_value = getattr(pixel_enum, name, None)
        if enum_value is not None and pf == enum_value:
            return (pixel_format, pixel_type, bytes_per_pixel)
    return None


def _classify_video_frame_format(
    fmt: "QVideoFrameFormat | None",
) -> tuple[int, int, int, int]:
    """Return shader enum values for *fmt*."""

    if fmt is None or QVideoFrameFormat is None:
        return (_VIDEO_FMT_NONE, _CS_BT709, _TF_SDR, _RANGE_LIMITED)

    pf = fmt.pixelFormat()
    pixel_enum = QVideoFrameFormat.PixelFormat
    format_nv12 = getattr(pixel_enum, "Format_NV12", None)
    format_p010 = getattr(pixel_enum, "Format_P010", None)
    if format_nv12 is not None and pf == format_nv12:
        pixel_fmt = _VIDEO_FMT_NV12
    elif format_p010 is not None and pf == format_p010:
        pixel_fmt = _VIDEO_FMT_P010
    else:
        pixel_fmt = _VIDEO_FMT_NONE

    cs = fmt.colorSpace()
    if cs == QVideoFrameFormat.ColorSpace.ColorSpace_BT2020:
        color_space = _CS_BT2020
    elif cs == QVideoFrameFormat.ColorSpace.ColorSpace_BT601:
        color_space = _CS_BT601
    else:
        color_space = _CS_BT709

    ct = fmt.colorTransfer()
    if ct == QVideoFrameFormat.ColorTransfer.ColorTransfer_ST2084:
        transfer = _TF_PQ
    elif ct == QVideoFrameFormat.ColorTransfer.ColorTransfer_STD_B67:
        transfer = _TF_HLG
    else:
        transfer = _TF_SDR

    cr = fmt.colorRange()
    if cr == QVideoFrameFormat.ColorRange.ColorRange_Full:
        color_range = _RANGE_FULL
    else:
        color_range = _RANGE_LIMITED

    return (pixel_fmt, color_space, transfer, color_range)


class TextureManager:
    """Manages the main image texture and auxiliary LUT textures."""

    def __init__(self, gl_funcs=None) -> None:
        """Create texture manager.

        Args:
            gl_funcs: Qt OpenGL functions object (for example QOpenGLFunctions_3_3_Core).
                When ``None``, PyOpenGL calls are used.
        """
        self._gl_funcs = gl_funcs
        self._use_pyopengl = gl_funcs is None
        self._texture_id: int = 0
        self._texture_width: int = 0
        self._texture_height: int = 0
        self._texture_uses_mipmaps: bool = True
        self._last_video_upload_pre_rotated: bool = False
        self._video_y_texture_id: int = 0
        self._video_uv_texture_id: int = 0
        self._video_y_shape: tuple[int, int, int] | None = None
        self._video_uv_shape: tuple[int, int, int] | None = None
        self._video_width: int = 0
        self._video_height: int = 0
        self._video_format: int = _VIDEO_FMT_NONE
        self._video_colorspace: int = _CS_BT709
        self._video_transfer: int = _TF_SDR
        self._video_range: int = _RANGE_LIMITED
        self._curve_lut_texture_id: int = 0
        self._levels_lut_texture_id: int = 0

    # ------------------------------------------------------------------
    # Main texture
    # ------------------------------------------------------------------
    def upload_texture(self, image: QImage) -> tuple[int, int, int]:
        """Upload *image* to the GPU and return ``(id, width, height)``."""

        if image.isNull():
            raise ValueError("Cannot upload a null QImage")
        self._delete_video_textures()

        if image.format() == QImage.Format.Format_RGBA8888:
            qimage = QImage(image)
        else:
            qimage = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = qimage.width(), qimage.height()
        buffer = qimage.constBits()
        byte_count = qimage.sizeInBytes()
        if hasattr(buffer, "setsize"):
            buffer.setsize(byte_count)
        else:
            buffer = buffer[:byte_count]

        self._ensure_source_texture(width, height, use_mipmaps=True)

        gl_funcs = self._gl_funcs or gl
        gl_funcs.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        row_length = qimage.bytesPerLine() // 4
        gl_funcs.glPixelStorei(gl.GL_UNPACK_ROW_LENGTH, row_length)
        gl_funcs.glTexSubImage2D(
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
        gl_funcs.glPixelStorei(gl.GL_UNPACK_ROW_LENGTH, 0)
        gl_funcs.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 4)
        gl_funcs.glGenerateMipmap(gl.GL_TEXTURE_2D)

        error = gl.glGetError()
        if error != gl.GL_NO_ERROR:
            _LOGGER.warning("OpenGL error after texture upload: 0x%04X", int(error))

        return self._texture_id, self._texture_width, self._texture_height

    def upload_video_frame(self, frame: "QVideoFrame") -> tuple[int, int]:
        """Upload *frame* directly as shader-readable textures."""

        if QVideoFrame is None or QVideoFrameFormat is None:
            raise RuntimeError("PySide6.QtMultimedia is required for video frame upload")
        if frame is None or not frame.isValid():
            raise ValueError("Cannot upload an invalid QVideoFrame")

        self._last_video_upload_pre_rotated = False
        fmt = frame.surfaceFormat()
        pixel_fmt, color_space, transfer, color_range = _classify_video_frame_format(fmt)
        if (
            sys.platform.startswith("linux")
            and pixel_fmt in (_VIDEO_FMT_NV12, _VIDEO_FMT_P010)
        ):
            image = frame.toImage()
            if not image.isNull():
                return self._upload_video_frame_as_image(image, fmt)

        packed_spec = _packed_frame_upload_spec(fmt)
        if packed_spec is not None:
            width = int(fmt.frameWidth())
            height = int(fmt.frameHeight())
            if width <= 0 or height <= 0:
                raise ValueError("Video frame dimensions are invalid")
            if not frame.map(QVideoFrame.MapMode.ReadOnly):
                raise RuntimeError("Failed to map packed QVideoFrame for reading")
            try:
                self._delete_video_textures()
                pixel_format, pixel_type, bytes_per_pixel = packed_spec
                self._upload_packed_texture(
                    width,
                    height,
                    pixel_format,
                    pixel_type,
                    frame.bytesPerLine(0),
                    bytes_per_pixel,
                    frame.bits(0),
                    height,
                )
            finally:
                frame.unmap()
            return self._texture_width, self._texture_height

        if pixel_fmt == _VIDEO_FMT_NONE:
            image = frame.toImage()
            if image.isNull():
                raise ValueError("Unsupported QVideoFrame could not be converted to QImage")
            return self._upload_video_frame_as_image(image, fmt)

        width = int(fmt.frameWidth())
        height = int(fmt.frameHeight())
        if width <= 0 or height <= 0:
            raise ValueError("Video frame dimensions are invalid")

        if not frame.map(QVideoFrame.MapMode.ReadOnly):
            raise RuntimeError("Failed to map QVideoFrame for reading")

        try:
            self._delete_image_texture()

            y_internal = gl.GL_R8 if pixel_fmt == _VIDEO_FMT_NV12 else gl.GL_R16
            y_format = gl.GL_RED
            y_type = gl.GL_UNSIGNED_BYTE if pixel_fmt == _VIDEO_FMT_NV12 else gl.GL_UNSIGNED_SHORT
            y_row_bytes = 1 if pixel_fmt == _VIDEO_FMT_NV12 else 2

            uv_internal = gl.GL_RG8 if pixel_fmt == _VIDEO_FMT_NV12 else gl.GL_RG16
            uv_format = gl.GL_RG
            uv_type = gl.GL_UNSIGNED_BYTE if pixel_fmt == _VIDEO_FMT_NV12 else gl.GL_UNSIGNED_SHORT
            uv_row_bytes = 2 if pixel_fmt == _VIDEO_FMT_NV12 else 4

            self._video_y_texture_id = self._ensure_plane_texture(
                self._video_y_texture_id,
                width,
                height,
                y_internal,
                self._video_y_shape,
            )
            self._video_y_shape = (width, height, y_internal)

            uv_width = max(width // 2, 1)
            uv_height = max(height // 2, 1)
            self._video_uv_texture_id = self._ensure_plane_texture(
                self._video_uv_texture_id,
                uv_width,
                uv_height,
                uv_internal,
                self._video_uv_shape,
            )
            self._video_uv_shape = (uv_width, uv_height, uv_internal)

            self._upload_plane(
                self._video_y_texture_id,
                width,
                height,
                y_format,
                y_type,
                frame.bytesPerLine(0),
                y_row_bytes,
                frame.bits(0),
                height,
            )
            gl.glGenerateMipmap(gl.GL_TEXTURE_2D)
            self._upload_plane(
                self._video_uv_texture_id,
                uv_width,
                uv_height,
                uv_format,
                uv_type,
                frame.bytesPerLine(1),
                uv_row_bytes,
                frame.bits(1),
                uv_height,
            )
            gl.glGenerateMipmap(gl.GL_TEXTURE_2D)
        finally:
            frame.unmap()

        self._video_width = width
        self._video_height = height
        self._video_format = pixel_fmt
        self._video_colorspace = color_space
        self._video_transfer = transfer
        self._video_range = color_range
        return self._video_width, self._video_height

    def _upload_video_frame_as_image(
        self,
        image: QImage,
        fmt: "QVideoFrameFormat | None",
    ) -> tuple[int, int]:
        """Upload a video frame via ``QImage`` conversion."""

        if image.isNull():
            raise ValueError("Unsupported QVideoFrame could not be converted to QImage")

        fmt_width = int(fmt.frameWidth()) if fmt is not None else 0
        fmt_height = int(fmt.frameHeight()) if fmt is not None else 0
        if fmt_width > 0 and fmt_height > 0:
            self._last_video_upload_pre_rotated = (
                image.width() == fmt_height and image.height() == fmt_width
            )
        self.upload_texture(image)
        return self._texture_width, self._texture_height

    def delete_texture(self) -> None:
        """Delete the currently bound source texture(s), if any."""

        self._delete_image_texture()
        self._delete_video_textures()

    def _delete_image_texture(self) -> None:
        if not self._texture_id:
            return
        gl_funcs = self._gl_funcs or gl
        delete_arg = self._delete_textures_arg(self._texture_id)
        gl_funcs.glDeleteTextures(1, delete_arg)
        self._texture_id = 0
        self._texture_width = 0
        self._texture_height = 0
        self._texture_uses_mipmaps = True

    def _delete_video_textures(self) -> None:
        if self._video_y_texture_id:
            gl.glDeleteTextures(1, np.array([int(self._video_y_texture_id)], dtype=np.uint32))
            self._video_y_texture_id = 0
        if self._video_uv_texture_id:
            gl.glDeleteTextures(1, np.array([int(self._video_uv_texture_id)], dtype=np.uint32))
            self._video_uv_texture_id = 0
        self._video_y_shape = None
        self._video_uv_shape = None
        self._video_width = 0
        self._video_height = 0
        self._video_format = _VIDEO_FMT_NONE
        self._video_colorspace = _CS_BT709
        self._video_transfer = _TF_SDR
        self._video_range = _RANGE_LIMITED

    def _ensure_plane_texture(
        self,
        texture_id: int,
        width: int,
        height: int,
        internal_format: int,
        current_shape: tuple[int, int, int] | None,
    ) -> int:
        recreate = (
            texture_id == 0
            or current_shape is None
            or current_shape != (width, height, internal_format)
        )
        if recreate and texture_id:
            gl.glDeleteTextures(1, np.array([int(texture_id)], dtype=np.uint32))
            texture_id = 0

        if not texture_id:
            created = gl.glGenTextures(1)
            if isinstance(created, (tuple, list)):
                created = created[0]
            texture_id = int(created)

        gl.glBindTexture(gl.GL_TEXTURE_2D, texture_id)
        if recreate:
            pixel_format = gl.GL_RED if internal_format in (gl.GL_R8, gl.GL_R16) else gl.GL_RG
            pixel_type = gl.GL_UNSIGNED_BYTE if internal_format in (gl.GL_R8, gl.GL_RG8) else gl.GL_UNSIGNED_SHORT
            gl.glTexImage2D(
                gl.GL_TEXTURE_2D,
                0,
                internal_format,
                width,
                height,
                0,
                pixel_format,
                pixel_type,
                None,
            )
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR_MIPMAP_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        return texture_id

    def _upload_plane(
        self,
        texture_id: int,
        width: int,
        height: int,
        pixel_format: int,
        pixel_type: int,
        bytes_per_line: int,
        bytes_per_pixel_group: int,
        bits,
        line_count: int,
    ) -> None:
        if bytes_per_line <= 0 or bits is None:
            raise ValueError("Video plane has invalid stride or data pointer")
        data_size = bytes_per_line * line_count
        if hasattr(bits, "setsize"):
            bits.setsize(data_size)
            data = bits
        else:
            data = bits[:data_size]
        gl.glBindTexture(gl.GL_TEXTURE_2D, texture_id)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        row_length = bytes_per_line // max(bytes_per_pixel_group, 1)
        gl.glPixelStorei(gl.GL_UNPACK_ROW_LENGTH, row_length)
        gl.glTexSubImage2D(
            gl.GL_TEXTURE_2D,
            0,
            0,
            0,
            width,
            height,
            pixel_format,
            pixel_type,
            data,
        )
        gl.glPixelStorei(gl.GL_UNPACK_ROW_LENGTH, 0)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 4)

    def _ensure_source_texture(
        self,
        width: int,
        height: int,
        *,
        use_mipmaps: bool,
    ) -> None:
        recreate = (
            not self._texture_id
            or self._texture_width != int(width)
            or self._texture_height != int(height)
            or self._texture_uses_mipmaps != bool(use_mipmaps)
        )
        gl_funcs = self._gl_funcs or gl
        if recreate:
            if self._texture_id:
                delete_arg = self._delete_textures_arg(self._texture_id)
                gl_funcs.glDeleteTextures(1, delete_arg)
                self._texture_id = 0
            tex_id = gl.glGenTextures(1)
            if isinstance(tex_id, (tuple, list)):
                tex_id = tex_id[0]
            self._texture_id = int(tex_id)
            self._texture_width = int(width)
            self._texture_height = int(height)
            self._texture_uses_mipmaps = bool(use_mipmaps)

        gl_funcs.glBindTexture(gl.GL_TEXTURE_2D, self._texture_id)
        if recreate:
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
            min_filter = gl.GL_LINEAR_MIPMAP_LINEAR if use_mipmaps else gl.GL_LINEAR
            gl_funcs.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, min_filter)
            gl_funcs.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            gl_funcs.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
            gl_funcs.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)

    def _delete_textures_arg(self, texture_id: int):
        """Return Qt/PyOpenGL-compatible argument for glDeleteTextures.

        Args:
            texture_id: OpenGL texture id to be deleted.

        Returns:
            Argument accepted by the active backend `glDeleteTextures` wrapper.
        """

        if self._use_pyopengl:
            return np.array([int(texture_id)], dtype=np.uint32)
        return [int(texture_id)]

    def _upload_packed_texture(
        self,
        width: int,
        height: int,
        pixel_format: int,
        pixel_type: int,
        bytes_per_line: int,
        bytes_per_pixel: int,
        bits,
        line_count: int,
    ) -> None:
        if bytes_per_line <= 0 or bits is None:
            raise ValueError("Packed video frame has invalid stride or data pointer")
        self._ensure_source_texture(width, height, use_mipmaps=True)

        data_size = bytes_per_line * line_count
        if hasattr(bits, "setsize"):
            bits.setsize(data_size)
            data = bits
        else:
            data = bits[:data_size]

        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        gl.glPixelStorei(gl.GL_UNPACK_ROW_LENGTH, bytes_per_line // max(bytes_per_pixel, 1))
        gl.glTexSubImage2D(
            gl.GL_TEXTURE_2D,
            0,
            0,
            0,
            width,
            height,
            pixel_format,
            pixel_type,
            data,
        )
        gl.glPixelStorei(gl.GL_UNPACK_ROW_LENGTH, 0)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 4)
        gl.glGenerateMipmap(gl.GL_TEXTURE_2D)

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

        return self._texture_id != 0 or self.has_video_texture()

    def texture_size(self) -> tuple[int, int]:
        """Return the uploaded texture dimensions as ``(width, height)``."""

        if self.has_video_texture():
            return self._video_width, self._video_height
        return self._texture_width, self._texture_height

    def has_video_texture(self) -> bool:
        """Return whether an uploaded YUV video texture pair is active."""

        return self._video_y_texture_id != 0 and self._video_uv_texture_id != 0

    def last_video_upload_pre_rotated(self) -> bool:
        """Return whether the latest fallback upload already contained rotation."""

        return self._last_video_upload_pre_rotated

    def video_texture_ids(self) -> tuple[int, int]:
        """Return ``(y_tex_id, uv_tex_id)`` for the active video texture pair."""

        return (self._video_y_texture_id, self._video_uv_texture_id)

    def video_metadata(self) -> tuple[int, int, int, int]:
        """Return the active video decode metadata consumed by the shader."""

        return (
            self._video_format,
            self._video_colorspace,
            self._video_transfer,
            self._video_range,
        )

    def destroy(self) -> None:
        """Delete all managed textures."""

        self.delete_texture()
        self._delete_curve_lut_texture()
        self._delete_levels_lut_texture()
