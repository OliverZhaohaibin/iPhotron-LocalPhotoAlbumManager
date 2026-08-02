# -*- coding: utf-8 -*-
"""GPU texture management for the GL renderer."""

from __future__ import annotations

import logging
import sys
from collections import OrderedDict

import numpy as np
from OpenGL import GL as gl
from PySide6.QtGui import QImage

from iPhoto.gui.detail_profile import emit_detail_event

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

    def __init__(self) -> None:
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
        self._still_textures: OrderedDict[object, tuple[int, int, int, int]] = OrderedDict()
        self._active_still_key: object | None = None
        self._still_budget_bytes = 192 * 1024 * 1024
        self._last_still_upload_result: dict[str, object] | None = None

    # ------------------------------------------------------------------
    # Main texture
    # ------------------------------------------------------------------
    def upload_texture(self, image: QImage) -> tuple[int, int, int]:
        """Upload *image* to the GPU and return ``(id, width, height)``."""

        if image.isNull():
            raise ValueError("Cannot upload a null QImage")
        if self._still_textures:
            self.clear_still_residency()
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
        gl.glGenerateMipmap(gl.GL_TEXTURE_2D)

        error = gl.glGetError()
        if error != gl.GL_NO_ERROR:
            _LOGGER.warning("OpenGL error after texture upload: 0x%04X", int(error))

        return self._texture_id, self._texture_width, self._texture_height

    def upload_still_texture(self, key: object, image: QImage) -> tuple[int, int, int]:
        """Upload or activate one non-mipmapped resident still texture."""

        if self.activate_still_texture(key):
            return self._texture_id, self._texture_width, self._texture_height
        self._upload_new_still_texture(key, image, activate=True)
        return self._texture_id, self._texture_width, self._texture_height

    def _upload_new_still_texture(
        self,
        key: object,
        image: QImage,
        *,
        activate: bool,
    ) -> bool:
        if image.isNull():
            raise ValueError("Cannot upload a null QImage")
        self._delete_video_textures()
        qimage = (
            QImage(image)
            if image.format() == QImage.Format.Format_RGBA8888
            else image.convertToFormat(QImage.Format.Format_RGBA8888)
        )
        width, height = qimage.width(), qimage.height()
        byte_count = max(0, int(qimage.bytesPerLine()) * height)
        resident_bytes = sum(entry[3] for entry in self._still_textures.values())
        replacement_required = bool(
            len(self._still_textures) >= 3 or resident_bytes + byte_count > self._still_budget_bytes
        )
        reusable_key = None
        if replacement_required:
            reusable_key = next(
                (
                    candidate
                    for candidate, entry in self._still_textures.items()
                    if candidate != self._active_still_key
                    and entry[1] == width
                    and entry[2] == height
                ),
                None,
            )
        old_entry = None
        if reusable_key is not None:
            old_entry = self._still_textures.pop(reusable_key)
            texture_id, _old_width, _old_height, old_size = old_entry
            allocate_storage = False
        else:
            self._evict_before_still_allocation(
                incoming_bytes=byte_count,
                resident_bytes=resident_bytes,
            )
            resident_bytes = sum(entry[3] for entry in self._still_textures.values())
            if (
                len(self._still_textures) >= 3
                or resident_bytes + byte_count > self._still_budget_bytes
            ):
                event = "gpu_texture_allocation_failed" if activate else "gpu_prefetch_dropped"
                emit_detail_event(
                    event,
                    generation=0,
                    width=width,
                    height=height,
                    bytes=byte_count,
                    reason="residency_budget",
                )
                self._last_still_upload_result = {
                    "key": key,
                    "activate": activate,
                    "success": False,
                    "reason": "residency_budget",
                }
                return False
            created = gl.glGenTextures(1)
            if isinstance(created, (tuple, list)):
                created = created[0]
            texture_id = int(created)
            if texture_id <= 0:
                self._record_still_upload_failure(
                    key,
                    activate=activate,
                    width=width,
                    height=height,
                    byte_count=byte_count,
                    reason="create_failed",
                )
                return False
            allocate_storage = True
        self._clear_gl_errors()
        try:
            gl.glBindTexture(gl.GL_TEXTURE_2D, texture_id)
            if allocate_storage:
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
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
            buffer = qimage.constBits()
            if hasattr(buffer, "setsize"):
                buffer.setsize(qimage.sizeInBytes())
            else:
                buffer = buffer[: qimage.sizeInBytes()]
            gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
            gl.glPixelStorei(gl.GL_UNPACK_ROW_LENGTH, qimage.bytesPerLine() // 4)
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
            upload_error = gl.glGetError()
        except Exception:  # noqa: BLE001 - PyOpenGL exposes backend-specific errors
            upload_error = -1
        finally:
            gl.glPixelStorei(gl.GL_UNPACK_ROW_LENGTH, 0)
            gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 4)
        if upload_error != gl.GL_NO_ERROR:
            if allocate_storage:
                gl.glDeleteTextures(1, np.array([int(texture_id)], dtype=np.uint32))
            elif reusable_key is not None and old_entry is not None:
                # A failed sub-image upload may have partially modified the
                # reused storage.  It is no longer a trustworthy resident copy.
                gl.glDeleteTextures(
                    1,
                    np.array([int(texture_id)], dtype=np.uint32),
                )
            self._record_still_upload_failure(
                key,
                activate=activate,
                width=width,
                height=height,
                byte_count=byte_count,
                reason="upload_failed",
            )
            return False
        self._still_textures[key] = (texture_id, width, height, byte_count)
        if reusable_key is not None:
            emit_detail_event(
                "gpu_evict",
                generation=0,
                bytes=old_size,
                reused=True,
            )
        if activate:
            self._active_still_key = key
            self._texture_id, self._texture_width, self._texture_height = (
                texture_id,
                width,
                height,
            )
            self._texture_uses_mipmaps = False
        self._trim_still_textures()
        self._last_still_upload_result = {
            "key": key,
            "activate": activate,
            "success": True,
            "reason": "uploaded",
        }
        return True

    def _record_still_upload_failure(
        self,
        key: object,
        *,
        activate: bool,
        width: int,
        height: int,
        byte_count: int,
        reason: str,
    ) -> None:
        event = "gpu_texture_allocation_failed" if activate else "gpu_prefetch_dropped"
        emit_detail_event(
            event,
            generation=0,
            width=width,
            height=height,
            bytes=byte_count,
            reason=reason,
        )
        self._last_still_upload_result = {
            "key": key,
            "activate": activate,
            "success": False,
            "reason": reason,
        }

    @staticmethod
    def _clear_gl_errors() -> None:
        for _attempt in range(8):
            if gl.glGetError() == gl.GL_NO_ERROR:
                return

    def take_still_upload_result(self) -> dict[str, object] | None:
        result = self._last_still_upload_result
        self._last_still_upload_result = None
        return result

    def _evict_before_still_allocation(
        self,
        *,
        incoming_bytes: int,
        resident_bytes: int,
    ) -> None:
        """Free non-active storage before allocating a differently-sized texture."""

        while (
            len(self._still_textures) >= 3
            or resident_bytes + incoming_bytes > self._still_budget_bytes
        ):
            victim = next(
                (
                    candidate
                    for candidate in self._still_textures
                    if candidate != self._active_still_key
                ),
                None,
            )
            if victim is None:
                break
            texture_id, _width, _height, size = self._still_textures.pop(victim)
            gl.glDeleteTextures(1, np.array([int(texture_id)], dtype=np.uint32))
            resident_bytes -= size
            emit_detail_event(
                "gpu_evict",
                generation=0,
                key=str(victim),
                bytes=size,
                before_allocate=True,
            )

    def activate_still_texture(self, key: object) -> bool:
        entry = self._still_textures.pop(key, None)
        if entry is None:
            return False
        self._still_textures[key] = entry
        texture_id, width, height, _size = entry
        self._active_still_key = key
        self._texture_id, self._texture_width, self._texture_height = texture_id, width, height
        self._texture_uses_mipmaps = False
        return True

    def touch_still_texture(self, key: object) -> bool:
        entry = self._still_textures.pop(key, None)
        if entry is None:
            return False
        self._still_textures[key] = entry
        return True

    def warm_still_texture(self, key: object, image: QImage) -> bool:
        if self.touch_still_texture(key):
            return False
        return self._upload_new_still_texture(key, image, activate=False)

    def has_still_texture(self, key: object) -> bool:
        return key in self._still_textures

    def texture_uses_mipmaps(self) -> bool:
        return self._texture_uses_mipmaps

    def _trim_still_textures(self) -> None:
        total = sum(entry[3] for entry in self._still_textures.values())
        while len(self._still_textures) > 3 or total > self._still_budget_bytes:
            victim = next(
                (
                    candidate
                    for candidate in self._still_textures
                    if candidate != self._active_still_key
                ),
                None,
            )
            if victim is None:
                break
            texture_id, _width, _height, size = self._still_textures.pop(victim)
            gl.glDeleteTextures(1, np.array([int(texture_id)], dtype=np.uint32))
            total -= size
            emit_detail_event("gpu_evict", generation=0, key=str(victim), bytes=size)

    def clear_still_residency(self) -> None:
        ids = [entry[0] for entry in self._still_textures.values()]
        if ids:
            gl.glDeleteTextures(len(ids), np.array(ids, dtype=np.uint32))
        self._still_textures.clear()
        self._active_still_key = None
        self._texture_id = 0
        self._texture_width = 0
        self._texture_height = 0

    def trim_still_residency(self) -> None:
        for key in tuple(self._still_textures):
            if key == self._active_still_key:
                continue
            texture_id, _width, _height, size = self._still_textures.pop(key)
            gl.glDeleteTextures(1, np.array([int(texture_id)], dtype=np.uint32))
            emit_detail_event("gpu_evict", generation=0, key=str(key), bytes=size, pressure=True)

    def upload_video_frame(self, frame: "QVideoFrame") -> tuple[int, int]:
        """Upload *frame* directly as shader-readable textures."""

        if QVideoFrame is None or QVideoFrameFormat is None:
            raise RuntimeError("PySide6.QtMultimedia is required for video frame upload")
        if frame is None or not frame.isValid():
            raise ValueError("Cannot upload an invalid QVideoFrame")

        self._last_video_upload_pre_rotated = False
        fmt = frame.surfaceFormat()
        pixel_fmt, color_space, transfer, color_range = _classify_video_frame_format(fmt)
        if sys.platform.startswith("linux") and pixel_fmt in (_VIDEO_FMT_NV12, _VIDEO_FMT_P010):
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
        self._texture_uses_mipmaps = True
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
        if self._still_textures:
            self.clear_still_residency()
            return
        if not self._texture_id:
            return
        gl.glDeleteTextures(1, np.array([int(self._texture_id)], dtype=np.uint32))
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
            pixel_type = (
                gl.GL_UNSIGNED_BYTE
                if internal_format in (gl.GL_R8, gl.GL_RG8)
                else gl.GL_UNSIGNED_SHORT
            )
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
            gl.glTexParameteri(
                gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR_MIPMAP_LINEAR
            )
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
        if recreate:
            if self._texture_id:
                gl.glDeleteTextures(1, np.array([int(self._texture_id)], dtype=np.uint32))
                self._texture_id = 0
            tex_id = gl.glGenTextures(1)
            if isinstance(tex_id, (tuple, list)):
                tex_id = tex_id[0]
            self._texture_id = int(tex_id)
            self._texture_width = int(width)
            self._texture_height = int(height)
            self._texture_uses_mipmaps = bool(use_mipmaps)

        gl.glBindTexture(gl.GL_TEXTURE_2D, self._texture_id)
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
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, min_filter)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)

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
