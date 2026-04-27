"""QRhi renderer for the image/video adjustment preview path.

This is the non-OpenGL implementation used by ``GLImageViewer`` on macOS.  It
keeps the viewer's public API intact while replacing raw OpenGL calls with a
Qt QRhi pipeline that can run on Metal.  The shader code is generated from the
same canonical adjustment source as the legacy OpenGL shader and packed into
QSB so Metal/OpenGL/HLSL targets share one calculation path.
"""

from __future__ import annotations

import logging
import math
import struct
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import (
    QColor,
    QImage,
    QRhi,
    QRhiBuffer,
    QRhiDepthStencilClearValue,
    QRhiGraphicsPipeline,
    QRhiSampler,
    QRhiShaderResourceBinding,
    QRhiShaderResourceBindings,
    QRhiShaderStage,
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
    QRhiVertexInputAttribute,
    QRhiVertexInputBinding,
    QRhiVertexInputLayout,
    QRhiViewport,
    QShader,
)

try:  # pragma: no cover - optional Qt module
    from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat
except (ModuleNotFoundError, ImportError):  # pragma: no cover
    QVideoFrame = None  # type: ignore[assignment, misc]
    QVideoFrameFormat = None  # type: ignore[assignment, misc]

from ....core.selective_color_resolver import NUM_RANGES, SAT_GATE_HI, SAT_GATE_LO

from .perspective_math import build_perspective_matrix

_LOGGER = logging.getLogger(__name__)

_SHADER_DIR = Path(__file__).resolve().parent
_IMAGE_VERT_QSB = _SHADER_DIR / "image_viewer_rhi.vert.qsb"
_IMAGE_FRAG_QSB = _SHADER_DIR / "image_viewer_rhi.frag.qsb"
_OVERLAY_VERT_QSB = _SHADER_DIR / "image_viewer_overlay.vert.qsb"
_OVERLAY_FRAG_QSB = _SHADER_DIR / "image_viewer_overlay.frag.qsb"

_UBO_SIZE = 480
_OVERLAY_VERTEX_STRIDE = 6 * 4
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


def _load_shader(path: Path) -> QShader:
    shader = QShader.fromSerialized(path.read_bytes())
    if not shader.isValid():
        raise RuntimeError(f"Failed to load shader: {path}")
    return shader


def _texture_format(name: str, fallback: QRhiTexture.Format) -> QRhiTexture.Format:
    return getattr(QRhiTexture.Format, name, fallback)


def _qimage_to_rgba(image: QImage) -> QImage:
    if image.format() == QImage.Format.Format_RGBA8888:
        return QImage(image)
    return image.convertToFormat(QImage.Format.Format_RGBA8888)


def _classify_video_frame_format(
    fmt: "QVideoFrameFormat | None",
) -> tuple[int, int, int, int]:
    if fmt is None or QVideoFrameFormat is None:
        return (_VIDEO_FMT_NONE, _CS_BT709, _TF_SDR, _RANGE_LIMITED)

    pf = fmt.pixelFormat()
    pixel_enum = QVideoFrameFormat.PixelFormat
    if getattr(pixel_enum, "Format_NV12", None) is not None and pf == pixel_enum.Format_NV12:
        pixel_fmt = _VIDEO_FMT_NV12
    elif getattr(pixel_enum, "Format_P010", None) is not None and pf == pixel_enum.Format_P010:
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
    color_range = _RANGE_FULL if cr == QVideoFrameFormat.ColorRange.ColorRange_Full else _RANGE_LIMITED
    return (pixel_fmt, color_space, transfer, color_range)


class RhiImageRenderer:
    """QRhi-backed replacement for the raw-GL ``GLRenderer`` on macOS."""

    def __init__(self) -> None:
        self._rhi: QRhi | None = None
        self._pipeline: QRhiGraphicsPipeline | None = None
        self._overlay_pipeline: QRhiGraphicsPipeline | None = None
        self._vbuf: QRhiBuffer | None = None
        self._overlay_vbuf: QRhiBuffer | None = None
        self._overlay_vbuf_capacity = 0
        self._ubuf: QRhiBuffer | None = None
        self._sampler: QRhiSampler | None = None
        self._srb: QRhiShaderResourceBindings | None = None

        self._tex_rgba: QRhiTexture | None = None
        self._tex_y: QRhiTexture | None = None
        self._tex_uv: QRhiTexture | None = None
        self._curve_lut_texture: QRhiTexture | None = None
        self._levels_lut_texture: QRhiTexture | None = None
        self._placeholder_texture: QRhiTexture | None = None
        self._placeholder_y_texture: QRhiTexture | None = None
        self._placeholder_uv_texture: QRhiTexture | None = None
        self._placeholder_lut_texture: QRhiTexture | None = None

        self._texture_width = 0
        self._texture_height = 0
        self._has_rgba_texture = False
        self._has_video_texture = False
        self._last_video_upload_pre_rotated = False
        self._video_format = _VIDEO_FMT_NONE
        self._video_colorspace = _CS_BT709
        self._video_transfer = _TF_SDR
        self._video_range = _RANGE_LIMITED
        self._tex_y_fmt: QRhiTexture.Format | None = None
        self._tex_uv_fmt: QRhiTexture.Format | None = None

        self._pending_rgba_image: QImage | None = None
        self._pending_video_planes: dict[str, Any] | None = None
        self._pending_curve_lut: np.ndarray | None = None
        self._pending_levels_lut: np.ndarray | None = None
        self._has_curve_lut = False
        self._has_levels_lut = False

    # ------------------------------------------------------------------
    # Resource lifecycle
    # ------------------------------------------------------------------
    def initialize_resources(self, rhi: QRhi, render_pass_descriptor, cb) -> None:
        self.destroy_resources()
        self._rhi = rhi

        vert_shader = _load_shader(_IMAGE_VERT_QSB)
        frag_shader = _load_shader(_IMAGE_FRAG_QSB)
        overlay_vert_shader = _load_shader(_OVERLAY_VERT_QSB)
        overlay_frag_shader = _load_shader(_OVERLAY_FRAG_QSB)

        vertices = [
            -1.0, -1.0, 0.0, 1.0,
            1.0, -1.0, 1.0, 1.0,
            -1.0, 1.0, 0.0, 0.0,
            1.0, -1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 0.0,
            -1.0, 1.0, 0.0, 0.0,
        ]
        vertex_data = struct.pack(f"{len(vertices)}f", *vertices)

        self._vbuf = rhi.newBuffer(
            QRhiBuffer.Type.Immutable,
            QRhiBuffer.UsageFlag.VertexBuffer,
            len(vertex_data),
        )
        self._vbuf.create()
        self._ubuf = rhi.newBuffer(
            QRhiBuffer.Type.Dynamic,
            QRhiBuffer.UsageFlag.UniformBuffer,
            _UBO_SIZE,
        )
        self._ubuf.create()
        self._sampler = rhi.newSampler(
            QRhiSampler.Filter.Linear,
            QRhiSampler.Filter.Linear,
            QRhiSampler.Filter.Linear,
            QRhiSampler.AddressMode.ClampToEdge,
            QRhiSampler.AddressMode.ClampToEdge,
        )
        self._sampler.create()

        self._placeholder_texture = self._create_texture(
            QRhiTexture.Format.RGBA8,
            QSize(2, 2),
        )
        self._placeholder_y_texture = self._create_texture(QRhiTexture.Format.R8, QSize(2, 2))
        self._placeholder_uv_texture = self._create_texture(QRhiTexture.Format.RG8, QSize(1, 1))
        self._placeholder_lut_texture = self._create_texture(QRhiTexture.Format.RGBA8, QSize(2, 1))
        self._tex_rgba = self._placeholder_texture
        self._tex_y = self._placeholder_y_texture
        self._tex_uv = self._placeholder_uv_texture
        self._curve_lut_texture = self._placeholder_lut_texture
        self._levels_lut_texture = self._placeholder_lut_texture

        self._srb = rhi.newShaderResourceBindings()
        self._rebuild_srb()

        self._pipeline = rhi.newGraphicsPipeline()
        self._pipeline.setShaderStages([
            QRhiShaderStage(QRhiShaderStage.Type.Vertex, vert_shader),
            QRhiShaderStage(QRhiShaderStage.Type.Fragment, frag_shader),
        ])
        target_blend = QRhiGraphicsPipeline.TargetBlend()
        target_blend.enable = False
        self._pipeline.setTargetBlends([target_blend])
        self._pipeline.setShaderResourceBindings(self._srb)
        self._pipeline.setRenderPassDescriptor(render_pass_descriptor)
        self._pipeline.setVertexInputLayout(self._main_vertex_layout())
        if self._pipeline.create() is False:
            raise RuntimeError("Failed to create QRhi image pipeline")

        self._overlay_pipeline = rhi.newGraphicsPipeline()
        self._overlay_pipeline.setShaderStages([
            QRhiShaderStage(QRhiShaderStage.Type.Vertex, overlay_vert_shader),
            QRhiShaderStage(QRhiShaderStage.Type.Fragment, overlay_frag_shader),
        ])
        overlay_blend = QRhiGraphicsPipeline.TargetBlend()
        overlay_blend.enable = True
        try:
            overlay_blend.srcColor = QRhiGraphicsPipeline.BlendFactor.SrcAlpha
            overlay_blend.dstColor = QRhiGraphicsPipeline.BlendFactor.OneMinusSrcAlpha
            overlay_blend.srcAlpha = QRhiGraphicsPipeline.BlendFactor.One
            overlay_blend.dstAlpha = QRhiGraphicsPipeline.BlendFactor.OneMinusSrcAlpha
        except Exception:  # pragma: no cover - enum names vary across PySide builds
            _LOGGER.debug("QRhi overlay blend factor assignment failed", exc_info=True)
        self._overlay_pipeline.setTargetBlends([overlay_blend])
        self._overlay_pipeline.setRenderPassDescriptor(render_pass_descriptor)
        self._overlay_pipeline.setVertexInputLayout(self._overlay_vertex_layout())
        if self._overlay_pipeline.create() is False:
            _LOGGER.warning("Failed to create QRhi crop overlay pipeline; crop overlay disabled")
            try:
                self._overlay_pipeline.destroy()
            except RuntimeError:
                pass
            self._overlay_pipeline = None

        ru = rhi.nextResourceUpdateBatch()
        ru.uploadStaticBuffer(self._vbuf, vertex_data)
        self._upload_placeholder_textures(ru)
        cb.resourceUpdate(ru)

    def destroy_resources(self) -> None:
        seen: set[int] = set()
        for resource in (
            self._overlay_vbuf,
            self._vbuf,
            self._ubuf,
            self._pipeline,
            self._overlay_pipeline,
            self._srb,
            self._sampler,
            self._tex_rgba,
            self._tex_y,
            self._tex_uv,
            self._curve_lut_texture,
            self._levels_lut_texture,
            self._placeholder_texture,
            self._placeholder_y_texture,
            self._placeholder_uv_texture,
            self._placeholder_lut_texture,
        ):
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            if resource is not None and hasattr(resource, "destroy"):
                try:
                    resource.destroy()
                except RuntimeError:
                    pass
        self.__init__()

    # ------------------------------------------------------------------
    # Texture API compatible with GLRenderer
    # ------------------------------------------------------------------
    def upload_texture(self, image: QImage) -> tuple[int, int, int]:
        if image.isNull():
            raise ValueError("Cannot upload a null QImage")
        qimage = _qimage_to_rgba(image)
        self._pending_rgba_image = qimage
        self._pending_video_planes = None
        self._has_rgba_texture = True
        self._has_video_texture = False
        self._texture_width = qimage.width()
        self._texture_height = qimage.height()
        self._video_format = _VIDEO_FMT_NONE
        return 0, self._texture_width, self._texture_height

    def upload_video_frame(self, frame: "QVideoFrame") -> tuple[int, int]:
        if QVideoFrame is None or QVideoFrameFormat is None:
            raise RuntimeError("PySide6.QtMultimedia is required for video frame upload")
        if frame is None or not frame.isValid():
            raise ValueError("Cannot upload an invalid QVideoFrame")

        self._last_video_upload_pre_rotated = False
        fmt = frame.surfaceFormat()
        pixel_fmt, color_space, transfer, color_range = _classify_video_frame_format(fmt)
        if pixel_fmt not in (_VIDEO_FMT_NV12, _VIDEO_FMT_P010):
            image = frame.toImage()
            if image.isNull():
                raise ValueError("Unsupported QVideoFrame could not be converted to QImage")
            self._last_video_upload_pre_rotated = self._is_prerotated_fallback(frame, image)
            self.upload_texture(image)
            return self._texture_width, self._texture_height

        if not frame.map(QVideoFrame.MapMode.ReadOnly):
            image = frame.toImage()
            if image.isNull():
                raise RuntimeError("Failed to map QVideoFrame for reading")
            self._last_video_upload_pre_rotated = self._is_prerotated_fallback(frame, image)
            self.upload_texture(image)
            return self._texture_width, self._texture_height

        try:
            width = int(fmt.frameWidth())
            height = int(fmt.frameHeight())
            if width <= 0 or height <= 0:
                raise ValueError("Video frame dimensions are invalid")

            uv_width = max(width // 2, 1)
            uv_height = max(height // 2, 1)
            y_stride = int(frame.bytesPerLine(0))
            uv_stride = int(frame.bytesPerLine(1))
            if y_stride <= 0 or uv_stride <= 0:
                raise ValueError("Video frame has invalid plane stride")
            y_size = y_stride * height
            uv_size = uv_stride * uv_height
            y_data = bytes(frame.bits(0)[:y_size])
            uv_data = bytes(frame.bits(1)[:uv_size])
        finally:
            frame.unmap()

        self._pending_video_planes = {
            "width": width,
            "height": height,
            "uv_width": uv_width,
            "uv_height": uv_height,
            "y_stride": y_stride,
            "uv_stride": uv_stride,
            "y_data": y_data,
            "uv_data": uv_data,
            "format": pixel_fmt,
        }
        self._pending_rgba_image = None
        self._has_rgba_texture = False
        self._has_video_texture = True
        self._texture_width = width
        self._texture_height = height
        self._video_format = pixel_fmt
        self._video_colorspace = color_space
        self._video_transfer = transfer
        self._video_range = color_range
        return width, height

    def last_video_upload_pre_rotated(self) -> bool:
        return self._last_video_upload_pre_rotated

    def delete_texture(self) -> None:
        self._pending_rgba_image = None
        self._pending_video_planes = None
        self._has_rgba_texture = False
        self._has_video_texture = False
        self._texture_width = 0
        self._texture_height = 0
        self._video_format = _VIDEO_FMT_NONE
        self._video_colorspace = _CS_BT709
        self._video_transfer = _TF_SDR
        self._video_range = _RANGE_LIMITED

    def upload_curve_lut(self, lut_data: np.ndarray) -> None:
        if lut_data is None or lut_data.shape != (256, 3):
            self._has_curve_lut = False
            self._pending_curve_lut = None
            return
        self._pending_curve_lut = np.ascontiguousarray(lut_data, dtype=np.float32)
        self._has_curve_lut = True

    def upload_levels_lut(self, lut_data: np.ndarray) -> None:
        if lut_data is None or lut_data.shape != (256, 3):
            self._has_levels_lut = False
            self._pending_levels_lut = None
            return
        self._pending_levels_lut = np.ascontiguousarray(lut_data, dtype=np.float32)
        self._has_levels_lut = True

    def has_texture(self) -> bool:
        return self._has_rgba_texture or self._has_video_texture

    def has_video_texture(self) -> bool:
        return self._has_video_texture

    def video_metadata(self) -> tuple[int, int, int, int]:
        return (
            self._video_format,
            self._video_colorspace,
            self._video_transfer,
            self._video_range,
        )

    def texture_size(self) -> tuple[int, int]:
        return self._texture_width, self._texture_height

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def render(
        self,
        *,
        cb,
        render_target,
        clear_color: QColor,
        view_width: float,
        view_height: float,
        scale: float,
        pan: QPointF,
        adjustments: Mapping[str, float],
        time_value: float | None = None,
        img_scale: float = 1.0,
        img_offset: QPointF | None = None,
        logical_tex_size: tuple[float, float] | None = None,
        corner_radius_px: float = 0.0,
        crop_rect: Mapping[str, float] | None = None,
        crop_faded: bool = False,
    ) -> None:
        if self._rhi is None or self._pipeline is None or self._srb is None or self._vbuf is None:
            cb.beginPass(render_target, clear_color, QRhiDepthStencilClearValue())
            cb.endPass()
            return

        output_size = render_target.pixelSize()
        if output_size.isEmpty():
            return

        ru = self._rhi.nextResourceUpdateBatch()
        self._flush_pending_texture_uploads(ru)
        self._update_uniforms(
            ru,
            view_width=view_width,
            view_height=view_height,
            scale=scale,
            pan=pan,
            adjustments=adjustments,
            time_value=time_value,
            img_scale=img_scale,
            img_offset=img_offset,
            logical_tex_size=logical_tex_size,
            corner_radius_px=corner_radius_px,
        )
        overlay_vertex_count = 0
        if crop_rect is not None:
            overlay_data = self._build_overlay_vertices(
                view_width=view_width,
                view_height=view_height,
                crop_rect=crop_rect,
                faded=crop_faded,
            )
            overlay_vertex_count = len(overlay_data) // 6
            if overlay_vertex_count:
                if self._ensure_overlay_buffer(len(overlay_data) * 4) and self._overlay_vbuf is not None:
                    ru.updateDynamicBuffer(
                        self._overlay_vbuf,
                        0,
                        len(overlay_data) * 4,
                        struct.pack(f"{len(overlay_data)}f", *overlay_data),
                    )
                else:
                    overlay_vertex_count = 0
        cb.resourceUpdate(ru)

        cb.beginPass(render_target, clear_color, QRhiDepthStencilClearValue())
        cb.setGraphicsPipeline(self._pipeline)
        cb.setShaderResources(self._srb)
        cb.setViewport(QRhiViewport(0, 0, output_size.width(), output_size.height()))
        cb.setVertexInput(0, [(self._vbuf, 0)])
        cb.draw(6)

        if overlay_vertex_count and self._overlay_pipeline is not None and self._overlay_vbuf is not None:
            cb.setGraphicsPipeline(self._overlay_pipeline)
            cb.setViewport(QRhiViewport(0, 0, output_size.width(), output_size.height()))
            cb.setVertexInput(0, [(self._overlay_vbuf, 0)])
            cb.draw(overlay_vertex_count)

        cb.endPass()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _is_prerotated_fallback(frame: "QVideoFrame", image: QImage) -> bool:
        try:
            fmt = frame.surfaceFormat()
        except (AttributeError, RuntimeError, TypeError):
            return False
        return (
            int(fmt.frameWidth()) > 0
            and int(fmt.frameHeight()) > 0
            and image.width() == int(fmt.frameHeight())
            and image.height() == int(fmt.frameWidth())
        )

    def _create_texture(
        self,
        fmt: QRhiTexture.Format,
        size: QSize,
        *,
        mipmapped: bool = False,
    ) -> QRhiTexture:
        if self._rhi is None:
            raise RuntimeError("QRhi is not available")
        if mipmapped:
            try:
                texture = self._rhi.newTexture(fmt, size, 1, QRhiTexture.Flag.MipMapped)
            except Exception:
                texture = self._rhi.newTexture(fmt, size)
        else:
            texture = self._rhi.newTexture(fmt, size)
        texture.create()
        return texture

    def _upload_placeholder_textures(self, ru) -> None:
        if self._placeholder_texture is not None:
            image = QImage(2, 2, QImage.Format.Format_RGBA8888)
            image.fill(QColor(0, 0, 0, 255))
            ru.uploadTexture(
                self._placeholder_texture,
                QRhiTextureUploadDescription(
                    QRhiTextureUploadEntry(0, 0, QRhiTextureSubresourceUploadDescription(image))
                ),
            )
        if self._placeholder_y_texture is not None:
            ru.uploadTexture(
                self._placeholder_y_texture,
                QRhiTextureUploadDescription(
                    QRhiTextureUploadEntry(0, 0, QRhiTextureSubresourceUploadDescription(bytes(4)))
                ),
            )
        if self._placeholder_uv_texture is not None:
            ru.uploadTexture(
                self._placeholder_uv_texture,
                QRhiTextureUploadDescription(
                    QRhiTextureUploadEntry(0, 0, QRhiTextureSubresourceUploadDescription(bytes([128, 128])))
                ),
            )
        if self._placeholder_lut_texture is not None:
            lut = QImage(2, 1, QImage.Format.Format_RGBA8888)
            lut.setPixelColor(0, 0, QColor(0, 0, 0, 255))
            lut.setPixelColor(1, 0, QColor(255, 255, 255, 255))
            ru.uploadTexture(
                self._placeholder_lut_texture,
                QRhiTextureUploadDescription(
                    QRhiTextureUploadEntry(0, 0, QRhiTextureSubresourceUploadDescription(lut))
                ),
            )

    @staticmethod
    def _main_vertex_layout() -> QRhiVertexInputLayout:
        input_layout = QRhiVertexInputLayout()
        input_layout.setBindings([QRhiVertexInputBinding(4 * 4)])
        input_layout.setAttributes([
            QRhiVertexInputAttribute(0, 0, QRhiVertexInputAttribute.Format.Float2, 0),
            QRhiVertexInputAttribute(0, 1, QRhiVertexInputAttribute.Format.Float2, 2 * 4),
        ])
        return input_layout

    @staticmethod
    def _overlay_vertex_layout() -> QRhiVertexInputLayout:
        input_layout = QRhiVertexInputLayout()
        input_layout.setBindings([QRhiVertexInputBinding(_OVERLAY_VERTEX_STRIDE)])
        input_layout.setAttributes([
            QRhiVertexInputAttribute(0, 0, QRhiVertexInputAttribute.Format.Float2, 0),
            QRhiVertexInputAttribute(0, 1, QRhiVertexInputAttribute.Format.Float4, 2 * 4),
        ])
        return input_layout

    def _rebuild_srb(self) -> None:
        if self._rhi is None or self._srb is None or self._ubuf is None or self._sampler is None:
            return
        self._srb.setBindings([
            QRhiShaderResourceBinding.uniformBuffer(
                0,
                QRhiShaderResourceBinding.StageFlag.FragmentStage,
                self._ubuf,
            ),
            QRhiShaderResourceBinding.sampledTexture(
                1,
                QRhiShaderResourceBinding.StageFlag.FragmentStage,
                self._tex_rgba or self._placeholder_texture,
                self._sampler,
            ),
            QRhiShaderResourceBinding.sampledTexture(
                2,
                QRhiShaderResourceBinding.StageFlag.FragmentStage,
                self._tex_y or self._placeholder_y_texture,
                self._sampler,
            ),
            QRhiShaderResourceBinding.sampledTexture(
                3,
                QRhiShaderResourceBinding.StageFlag.FragmentStage,
                self._tex_uv or self._placeholder_uv_texture,
                self._sampler,
            ),
            QRhiShaderResourceBinding.sampledTexture(
                4,
                QRhiShaderResourceBinding.StageFlag.FragmentStage,
                self._curve_lut_texture or self._placeholder_lut_texture,
                self._sampler,
            ),
            QRhiShaderResourceBinding.sampledTexture(
                5,
                QRhiShaderResourceBinding.StageFlag.FragmentStage,
                self._levels_lut_texture or self._placeholder_lut_texture,
                self._sampler,
            ),
        ])
        self._srb.create()

    def _flush_pending_texture_uploads(self, ru) -> None:
        if self._rhi is None:
            return
        if self._pending_rgba_image is not None:
            image = self._pending_rgba_image
            size = QSize(image.width(), image.height())
            if (
                self._tex_rgba is self._placeholder_texture
                or self._tex_rgba is None
                or self._tex_rgba.pixelSize() != size
            ):
                if self._tex_rgba is not None and self._tex_rgba is not self._placeholder_texture:
                    self._tex_rgba.destroy()
                self._tex_rgba = self._create_texture(QRhiTexture.Format.RGBA8, size, mipmapped=True)
                self._rebuild_srb()
            ru.uploadTexture(
                self._tex_rgba,
                QRhiTextureUploadDescription(
                    QRhiTextureUploadEntry(0, 0, QRhiTextureSubresourceUploadDescription(image))
                ),
            )
            if hasattr(ru, "generateMips"):
                try:
                    ru.generateMips(self._tex_rgba)
                except Exception:
                    _LOGGER.debug("QRhi mip generation failed for image texture", exc_info=True)
            self._pending_rgba_image = None

        if self._pending_video_planes is not None:
            planes = self._pending_video_planes
            pixel_fmt = int(planes["format"])
            y_fmt = QRhiTexture.Format.R8 if pixel_fmt == _VIDEO_FMT_NV12 else QRhiTexture.Format.R16
            uv_fmt = QRhiTexture.Format.RG8 if pixel_fmt == _VIDEO_FMT_NV12 else QRhiTexture.Format.RG16
            y_size = QSize(int(planes["width"]), int(planes["height"]))
            uv_size = QSize(int(planes["uv_width"]), int(planes["uv_height"]))
            if self._tex_y is self._placeholder_y_texture or self._tex_y is None or self._tex_y.pixelSize() != y_size or self._tex_y_fmt != y_fmt:
                if self._tex_y is not None and self._tex_y is not self._placeholder_y_texture:
                    self._tex_y.destroy()
                self._tex_y = self._create_texture(y_fmt, y_size, mipmapped=True)
                self._tex_y_fmt = y_fmt
                self._rebuild_srb()
            if self._tex_uv is self._placeholder_uv_texture or self._tex_uv is None or self._tex_uv.pixelSize() != uv_size or self._tex_uv_fmt != uv_fmt:
                if self._tex_uv is not None and self._tex_uv is not self._placeholder_uv_texture:
                    self._tex_uv.destroy()
                self._tex_uv = self._create_texture(uv_fmt, uv_size, mipmapped=True)
                self._tex_uv_fmt = uv_fmt
                self._rebuild_srb()
            y_sub = QRhiTextureSubresourceUploadDescription(planes["y_data"])
            y_sub.setDataStride(int(planes["y_stride"]))
            ru.uploadTexture(self._tex_y, QRhiTextureUploadDescription(QRhiTextureUploadEntry(0, 0, y_sub)))
            uv_sub = QRhiTextureSubresourceUploadDescription(planes["uv_data"])
            uv_sub.setDataStride(int(planes["uv_stride"]))
            ru.uploadTexture(self._tex_uv, QRhiTextureUploadDescription(QRhiTextureUploadEntry(0, 0, uv_sub)))
            if hasattr(ru, "generateMips"):
                for texture in (self._tex_y, self._tex_uv):
                    try:
                        ru.generateMips(texture)
                    except Exception:
                        _LOGGER.debug("QRhi mip generation failed for video plane", exc_info=True)
            self._pending_video_planes = None

        if self._pending_curve_lut is not None:
            self._curve_lut_texture = self._upload_lut_texture(
                ru,
                self._curve_lut_texture,
                self._pending_curve_lut,
            )
            self._pending_curve_lut = None
            self._rebuild_srb()
        if self._pending_levels_lut is not None:
            self._levels_lut_texture = self._upload_lut_texture(
                ru,
                self._levels_lut_texture,
                self._pending_levels_lut,
            )
            self._pending_levels_lut = None
            self._rebuild_srb()

    def _upload_lut_texture(self, ru, current: QRhiTexture | None, lut: np.ndarray) -> QRhiTexture:
        lut_format = _texture_format("RGBA32F", QRhiTexture.Format.RGBA8)
        if current is not None and current is not self._placeholder_lut_texture:
            current.destroy()
        if lut_format == QRhiTexture.Format.RGBA8:
            lut_image = QImage(256, 1, QImage.Format.Format_RGBA8888)
            clamped = np.clip(lut, 0.0, 1.0)
            for idx in range(256):
                r, g, b = (int(round(float(channel) * 255.0)) for channel in clamped[idx])
                lut_image.setPixelColor(idx, 0, QColor(r, g, b, 255))
            texture = self._create_texture(QRhiTexture.Format.RGBA8, QSize(256, 1))
            sub = QRhiTextureSubresourceUploadDescription(lut_image)
        else:
            rgba = np.ones((256, 4), dtype=np.float32)
            rgba[:, :3] = np.clip(lut, 0.0, 1.0)
            texture = self._create_texture(lut_format, QSize(256, 1))
            sub = QRhiTextureSubresourceUploadDescription(rgba.tobytes())
            sub.setDataStride(256 * 4 * 4)
        ru.uploadTexture(texture, QRhiTextureUploadDescription(QRhiTextureUploadEntry(0, 0, sub)))
        return texture

    def _update_uniforms(
        self,
        ru,
        *,
        view_width: float,
        view_height: float,
        scale: float,
        pan: QPointF,
        adjustments: Mapping[str, float],
        time_value: float | None,
        img_scale: float,
        img_offset: QPointF | None,
        logical_tex_size: tuple[float, float] | None,
        corner_radius_px: float,
    ) -> None:
        def value(key: str, default: float = 0.0) -> float:
            return float(adjustments.get(key, default))

        data = bytearray(_UBO_SIZE)
        video_format, video_colorspace, video_transfer, video_range = (
            self.video_metadata() if self._has_video_texture else (_VIDEO_FMT_NONE, _CS_BT709, _TF_SDR, _RANGE_LIMITED)
        )
        ints = [
            1 if self._has_video_texture else 0,
            int(video_format),
            int(video_colorspace),
            int(video_transfer),
            int(video_range),
            int(float(adjustments.get("Crop_Rotate90", 0.0))) % 4,
            1 if bool(adjustments.get("BW_Enabled", adjustments.get("BWEnabled", 0.0))) else 0,
            1 if bool(adjustments.get("Curve_Enabled", False)) and self._has_curve_lut else 0,
            1 if bool(adjustments.get("Levels_Enabled", False)) and self._has_levels_lut else 0,
            1 if bool(adjustments.get("WB_Enabled", adjustments.get("WBEnabled", 0.0))) else 0,
            1 if bool(adjustments.get("SelectiveColor_Enabled", False)) else 0,
        ]
        for index, int_value in enumerate(ints):
            struct.pack_into("i", data, index * 4, int_value)

        def pack_float(offset: int, float_value: float) -> None:
            struct.pack_into("f", data, offset, float(float_value))

        scalar_offsets = {
            44: value("Brilliance"),
            48: value("Exposure"),
            52: value("Highlights"),
            56: value("Shadows"),
            60: value("Brightness"),
            64: value("Contrast"),
            68: value("BlackPoint"),
            72: value("Saturation"),
            76: value("Vibrance"),
            80: value("Cast"),
            84: value("WBWarmth"),
            88: value("WBTemperature"),
            92: value("WBTint"),
            96: 0.0 if time_value is None else float(time_value),
            100: float(adjustments.get("Definition_Value", 0.0)) * 0.2
            if bool(adjustments.get("Definition_Enabled", False))
            else 0.0,
            104: float(adjustments.get("Denoise_Amount", 0.0))
            if bool(adjustments.get("Denoise_Enabled", False))
            else 0.0,
            108: float(adjustments.get("Sharpen_Intensity", 0.0))
            if bool(adjustments.get("Sharpen_Enabled", False))
            else 0.0,
            112: float(adjustments.get("Sharpen_Edges", 0.0))
            if bool(adjustments.get("Sharpen_Enabled", False))
            else 0.0,
            116: float(adjustments.get("Sharpen_Falloff", 0.0))
            if bool(adjustments.get("Sharpen_Enabled", False))
            else 0.0,
            120: float(adjustments.get("Vignette_Strength", 0.0))
            if bool(adjustments.get("Vignette_Enabled", False))
            else 0.0,
            124: float(adjustments.get("Vignette_Radius", 0.50))
            if bool(adjustments.get("Vignette_Enabled", False))
            else 0.50,
            128: 0.1
            + max(0.0, min(1.0, float(adjustments.get("Vignette_Softness", 0.0)))) * 0.9
            if bool(adjustments.get("Vignette_Enabled", False))
            else 0.1,
            132: max(float(scale), 1e-6),
            136: max(float(img_scale), 1e-6),
            140: max(0.0, float(corner_radius_px)),
            144: value("Crop_CX", 0.5),
            148: value("Crop_CY", 0.5),
            152: value("Crop_W", 1.0),
            156: value("Crop_H", 1.0),
        }
        for offset, float_value in scalar_offsets.items():
            pack_float(offset, float_value)

        struct.pack_into(
            "3f",
            data,
            160,
            float(adjustments.get("Color_Gain_R", 1.0)),
            float(adjustments.get("Color_Gain_G", 1.0)),
            float(adjustments.get("Color_Gain_B", 1.0)),
        )
        struct.pack_into(
            "4f",
            data,
            176,
            value("BWIntensity"),
            value("BWNeutrals"),
            value("BWTone"),
            value("BWGrain"),
        )

        if logical_tex_size is None:
            logical_w = float(max(1, self._texture_width))
            logical_h = float(max(1, self._texture_height))
        else:
            logical_w, logical_h = logical_tex_size
        offset_value = img_offset or QPointF(0.0, 0.0)
        struct.pack_into("2f", data, 192, max(float(view_width), 1.0), max(float(view_height), 1.0))
        struct.pack_into("2f", data, 200, max(float(logical_w), 1.0), max(float(logical_h), 1.0))
        struct.pack_into("2f", data, 208, float(pan.x()), float(pan.y()))
        struct.pack_into("2f", data, 216, float(offset_value.x()), float(offset_value.y()))

        logical_aspect_ratio = float(logical_w) / float(logical_h) if float(logical_h) > 0.0 else 1.0
        if not math.isfinite(logical_aspect_ratio) or logical_aspect_ratio <= 1e-6:
            logical_aspect_ratio = 1.0
        perspective_matrix = build_perspective_matrix(
            value("Perspective_Vertical", 0.0),
            value("Perspective_Horizontal", 0.0),
            image_aspect_ratio=logical_aspect_ratio,
            straighten_degrees=value("Crop_Straighten", 0.0),
            rotate_steps=0,
            flip_horizontal=bool(adjustments.get("Crop_FlipH", False)),
        )
        struct.pack_into("3f", data, 224, *perspective_matrix[0])
        struct.pack_into("3f", data, 240, *perspective_matrix[1])
        struct.pack_into("3f", data, 256, *perspective_matrix[2])

        sc_ranges = adjustments.get("SelectiveColor_Ranges")
        selective_color_u0 = np.zeros((NUM_RANGES, 4), dtype=np.float32)
        selective_color_u1 = np.zeros((NUM_RANGES, 4), dtype=np.float32)
        if isinstance(sc_ranges, list) and len(sc_ranges) == NUM_RANGES:
            for idx, rng in enumerate(sc_ranges):
                if isinstance(rng, (list, tuple)) and len(rng) >= 5:
                    center = float(rng[0])
                    range_slider = float(np.clip(rng[1], 0.0, 1.0))
                    deg = 5.0 + (70.0 - 5.0) * range_slider
                    width_hue = float(np.clip(deg / 360.0, 0.001, 0.5))
                    selective_color_u0[idx] = [center, width_hue, float(rng[2]), float(rng[3])]
                    selective_color_u1[idx] = [float(rng[4]), SAT_GATE_LO, SAT_GATE_HI, 1.0]
        for idx in range(NUM_RANGES):
            struct.pack_into("4f", data, 272 + idx * 16, *selective_color_u0[idx])
            struct.pack_into("4f", data, 368 + idx * 16, *selective_color_u1[idx])
        struct.pack_into("i", data, 464, 1)

        ru.updateDynamicBuffer(self._ubuf, 0, len(data), bytes(data))

    def _ensure_overlay_buffer(self, required_size: int) -> bool:
        if self._rhi is None or required_size <= 0:
            return False
        if self._overlay_vbuf is not None and self._overlay_vbuf_capacity >= required_size:
            return True
        if self._overlay_vbuf is not None:
            try:
                self._overlay_vbuf.destroy()
            except RuntimeError:
                pass
        self._overlay_vbuf = None
        self._overlay_vbuf_capacity = 0

        capacity = max(required_size, 4096)
        overlay_vbuf = self._rhi.newBuffer(
            QRhiBuffer.Type.Dynamic,
            QRhiBuffer.UsageFlag.VertexBuffer,
            capacity,
        )
        if overlay_vbuf is None:
            _LOGGER.warning("Failed to allocate QRhi crop overlay buffer")
            return False
        if overlay_vbuf.create() is False:
            _LOGGER.warning("Failed to create QRhi crop overlay buffer")
            try:
                overlay_vbuf.destroy()
            except RuntimeError:
                pass
            return False

        self._overlay_vbuf = overlay_vbuf
        self._overlay_vbuf_capacity = capacity
        return True

    @staticmethod
    def _build_overlay_vertices(
        *,
        view_width: float,
        view_height: float,
        crop_rect: Mapping[str, float],
        faded: bool,
    ) -> list[float]:
        try:
            vw = float(view_width)
            vh = float(view_height)
        except (TypeError, ValueError):
            return []
        if not math.isfinite(vw) or not math.isfinite(vh) or vw <= 0.0 or vh <= 0.0:
            return []

        def finite_rect_value(key: str, fallback: float) -> float | None:
            try:
                value = float(crop_rect.get(key, fallback))
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) else None

        left = finite_rect_value("left", 0.0)
        right = finite_rect_value("right", vw)
        top = finite_rect_value("top", 0.0)
        bottom = finite_rect_value("bottom", vh)
        if left is None or right is None or top is None or bottom is None:
            return []

        left, right = sorted((left, right))
        top, bottom = sorted((top, bottom))
        left = min(max(left, 0.0), vw)
        right = min(max(right, 0.0), vw)
        top = min(max(top, 0.0), vh)
        bottom = min(max(bottom, 0.0), vh)
        if right <= left or bottom <= top:
            return []

        overlay_colour = (0.0, 0.0, 0.0, 1.0 if faded else 0.55)
        border_colour = (1.0, 0.85, 0.2, 1.0)
        vertices: list[float] = []

        def add_rect(rect: tuple[float, float, float, float], colour: tuple[float, float, float, float]) -> None:
            l_px, t_px, r_px, b_px = rect
            l_px = min(max(l_px, 0.0), vw)
            r_px = min(max(r_px, 0.0), vw)
            t_px = min(max(t_px, 0.0), vh)
            b_px = min(max(b_px, 0.0), vh)
            if r_px <= l_px or b_px <= t_px:
                return
            points = [
                (l_px, t_px),
                (r_px, t_px),
                (l_px, b_px),
                (r_px, t_px),
                (r_px, b_px),
                (l_px, b_px),
            ]
            for px, py in points:
                vertices.extend(((2.0 * px / vw) - 1.0, 1.0 - (2.0 * py / vh), *colour))

        for quad in (
            (0.0, 0.0, vw, top),
            (0.0, bottom, vw, vh),
            (0.0, top, left, bottom),
            (right, top, vw, bottom),
        ):
            add_rect(quad, overlay_colour)

        if not faded:
            border = 2.0
            add_rect((left, top, right, top + border), border_colour)
            add_rect((left, bottom - border, right, bottom), border_colour)
            add_rect((left, top, left + border, bottom), border_colour)
            add_rect((right - border, top, right, bottom), border_colour)

            handle_size = 7.0
            for cx, cy in ((left, top), (right, top), (right, bottom), (left, bottom)):
                add_rect(
                    (
                        cx - handle_size,
                        cy - handle_size,
                        cx + handle_size,
                        cy + handle_size,
                    ),
                    border_colour,
                )
        return vertices
