"""GPU-accelerated video renderer using QRhiWidget.

Receives decoded ``QVideoFrame`` objects from a ``QVideoSink``, uploads the
frame data as GPU textures and renders via custom shaders that handle:

* YUV (NV12 / P010) → RGB colour-space conversion
* Correct BT.601 / BT.709 / BT.2020 matrix selection
* Limited-range vs full-range normalisation
* HDR→SDR tone mapping for PQ (ST.2084) and HLG (STD-B67) content
* Letterbox rendering with a configurable background colour
* Always-opaque output (alpha = 1.0)

The widget replaces ``QGraphicsVideoItem`` / ``QVideoWidget`` to give the
application full control over the rendering pipeline, independent of any
parent-widget background colour.
"""

from __future__ import annotations

import logging
import os
import struct
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, QSizeF, Qt, Signal
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
from PySide6.QtWidgets import QRhiWidget, QWidget

try:
    from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat, QVideoSink
except (ModuleNotFoundError, ImportError):  # pragma: no cover
    QVideoFrame = None  # type: ignore[assignment, misc]
    QVideoFrameFormat = None  # type: ignore[assignment, misc]
    QVideoSink = None  # type: ignore[assignment, misc]

_log = logging.getLogger(__name__)

# Shader .qsb files live next to this module.
_SHADER_DIR = Path(__file__).resolve().parent
_VERT_QSB = _SHADER_DIR / "video_renderer.vert.qsb"
_FRAG_QSB = _SHADER_DIR / "video_renderer.frag.qsb"

# Uniform block layout (std140, binding 0):
#   int u_format;          // offset  0
#   int u_colorspace;      // offset  4
#   int u_transfer;        // offset  8
#   int u_range;           // offset 12
#   vec4 u_letterbox_color;// offset 16  (4 floats)
#   vec4 u_video_rect;     // offset 32  (4 floats)
#   int u_rotate90;        // offset 48
#   int u_mirror;          // offset 52
#   int _pad0;             // offset 56
#   int _pad1;             // offset 60
#                          // total = 64 bytes
_UBO_SIZE = 64


def _load_shader(path: Path) -> QShader:
    """Load a pre-compiled QSB shader from *path*."""
    data = path.read_bytes()
    shader = QShader.fromSerialized(data)
    if not shader.isValid():
        raise RuntimeError(f"Failed to load shader: {path}")
    return shader


# Format enum values matching the shader
_FMT_NV12 = 0
_FMT_P010 = 1
_FMT_RGBA = 2

# Color space enum
_CS_BT601 = 0
_CS_BT709 = 1
_CS_BT2020 = 2

# Transfer function enum
_TF_SDR = 0
_TF_PQ = 1
_TF_HLG = 2

# Range enum
_RANGE_LIMITED = 0
_RANGE_FULL = 1


def _should_assume_linux_180_prerotated(container_hint: bool) -> bool:
    """Return whether Linux 180° streams should be treated as pre-rotated.

    ``QVideoFrameFormat.rotation()`` reports metadata, not post-decode pixel
    orientation, so it is insufficient on its own to conclude that a Linux
    frame has already been pre-rotated. Restrict this 180° workaround to
    backends/environments that explicitly opt in.
    """

    if not sys.platform.startswith("linux"):
        return False

    backend = os.environ.get("QT_MEDIA_BACKEND", "").strip().lower()
    if backend in {"gstreamer", "ffmpeg"}:
        return True

    if container_hint:
        return True

    forced = os.environ.get("IPHOTRON_ASSUME_LINUX_180_PREROTATED", "")
    return forced.strip().lower() in {"1", "true", "yes", "on"}


def _classify_frame_format(fmt: "QVideoFrameFormat") -> tuple[int, int, int, int]:
    """Return (format, colorspace, transfer, range) shader enum values for *fmt*."""

    if QVideoFrameFormat is None:
        return _FMT_RGBA, _CS_BT709, _TF_SDR, _RANGE_LIMITED

    # Pixel format
    pf = fmt.pixelFormat()
    if pf == QVideoFrameFormat.PixelFormat.Format_NV12:
        pixel_fmt = _FMT_NV12
    elif pf == QVideoFrameFormat.PixelFormat.Format_P010:
        pixel_fmt = _FMT_P010
    else:
        # Treat everything else as RGBA (the frame will be converted via toImage)
        pixel_fmt = _FMT_RGBA

    # Color space
    cs = fmt.colorSpace()
    if cs == QVideoFrameFormat.ColorSpace.ColorSpace_BT2020:
        color_space = _CS_BT2020
    elif cs == QVideoFrameFormat.ColorSpace.ColorSpace_BT601:
        color_space = _CS_BT601
    else:
        color_space = _CS_BT709

    # Transfer function
    ct = fmt.colorTransfer()
    if ct == QVideoFrameFormat.ColorTransfer.ColorTransfer_ST2084:
        transfer = _TF_PQ
    elif ct == QVideoFrameFormat.ColorTransfer.ColorTransfer_STD_B67:
        transfer = _TF_HLG
    else:
        transfer = _TF_SDR

    # Range
    cr = fmt.colorRange()
    if cr == QVideoFrameFormat.ColorRange.ColorRange_Full:
        color_range = _RANGE_FULL
    else:
        color_range = _RANGE_LIMITED

    return pixel_fmt, color_space, transfer, color_range


class VideoRendererWidget(QRhiWidget):
    """Hardware-accelerated video display widget using the QRhi abstraction.

    Accepts video frames via :meth:`update_frame` and renders them with
    correct colour-science handling, independent of the widget's background.

    Signals
    -------
    nativeSizeChanged(QSizeF)
        Emitted when the decoded video resolution changes.
    firstFrameReady()
        Emitted once after the first opaque frame has been rendered.
    """

    nativeSizeChanged = Signal(QSizeF)
    firstFrameReady = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # Force the OpenGL backend so both QRhiWidget-based renderers
        # (image viewer and video renderer) share the same rendering
        # infrastructure inside the QStackedWidget.
        # Must be called in the constructor — Qt docs state that calling
        # setApi() after the widget is shown may have no effect.
        self.setApi(QRhiWidget.Api.OpenGL)

        # Declare that this widget always produces fully opaque output so
        # the compositor never expects transparency from the first paint.
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        # Prevent the main window's WA_TranslucentBackground from cascading
        # into this widget and causing transparent first-frame flashes.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        # --- state ---
        self._letterbox_color = QColor("#e8e8e8")
        self._current_frame: Optional["QVideoFrame"] = None
        self._frame_dirty = False
        self._native_size = QSizeF()
        self._first_render_done = False
        self._has_frame = False
        self._viewport_fill_enabled = False

        # --- RHI resources (created in initialize()) ---
        self._pipeline: Optional[QRhiGraphicsPipeline] = None
        self._vbuf: Optional[QRhiBuffer] = None
        self._ubuf: Optional[QRhiBuffer] = None
        self._sampler: Optional[QRhiSampler] = None
        self._srb: Optional[QRhiShaderResourceBindings] = None
        self._tex_y: Optional[QRhiTexture] = None
        self._tex_uv: Optional[QRhiTexture] = None
        self._tex_rgba: Optional[QRhiTexture] = None
        self._tex_y_fmt: Optional[QRhiTexture.Format] = None
        self._tex_uv_fmt: Optional[QRhiTexture.Format] = None
        self._initialized = False

        # Frame metadata (updated per frame)
        self._fmt_enum = _FMT_RGBA
        self._cs_enum = _CS_BT709
        self._tf_enum = _TF_SDR
        self._range_enum = _RANGE_LIMITED
        self._rotate90_steps = 0
        self._mirror = 0

        # Container-level rotation obtained from ffprobe.  Used as the
        # primary rotation source on all platforms because Qt's
        # ``QVideoFrameFormat.rotation()`` can report platform-dependent
        # values.  When ffprobe data is unavailable the code falls back to
        # Qt's value.
        self._container_rotation_cw: int = 0
        self._container_raw_w: int = 0
        self._container_raw_h: int = 0
        self._container_linux_180_hint: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_container_rotation(
        self,
        cw_degrees: int,
        raw_w: int,
        raw_h: int,
        linux_180_hint: bool = False,
    ) -> None:
        """Store the container-level display-matrix rotation from ffprobe.

        Parameters
        ----------
        cw_degrees:
            Clockwise rotation in degrees (0, 90, 180, 270) that should be
            applied to the raw decoded frame for correct display.
        raw_w, raw_h:
            Coded pixel dimensions of the video stream *before* rotation.
            These are used to detect whether the multimedia backend has
            already pre-rotated the decoded frames (by comparing against
            the frame dimensions reported by ``QVideoFrameFormat``).
        linux_180_hint:
            Optional probe hint for 180° clips that commonly arrive already
            upright on Linux backends (e.g. some Apple QuickTime sources).
        """

        self._container_rotation_cw = cw_degrees
        self._container_raw_w = raw_w
        self._container_raw_h = raw_h
        self._container_linux_180_hint = bool(linux_180_hint)

    def set_linux_180_hint(self, enabled: bool) -> None:
        """Set whether Linux 180° pre-rotation workaround is hinted."""
        self._container_linux_180_hint = bool(enabled)

    def set_viewport_fill_enabled(self, enabled: bool) -> None:
        """Control whether the video covers the viewport instead of letterboxing."""

        target = bool(enabled)
        if self._viewport_fill_enabled == target:
            return
        self._viewport_fill_enabled = target
        self.update()

    def update_frame(self, frame: "QVideoFrame") -> None:
        """Accept a new video frame and schedule a repaint."""
        if frame is None or not frame.isValid():
            return
        self._current_frame = frame
        self._frame_dirty = True
        self._has_frame = True

        # Check for resolution change
        fmt = frame.surfaceFormat()
        w = fmt.frameWidth()
        h = fmt.frameHeight()

        # ---- Rotation resolution -------------------------------------------
        # Qt's ``QVideoFrameFormat.rotation()`` value can differ across
        # platforms: on Windows (Media Foundation) it usually reports the
        # correct clockwise rotation from the display matrix; on Linux
        # (FFmpeg / GStreamer backends) the value may be wrong (e.g. negated
        # sign) or the backend may pre-rotate the pixel data while *still*
        # reporting the original rotation, causing double rotation.
        #
        # The most reliable cross-platform source of truth is the ffprobe
        # display-matrix metadata stored in ``_container_rotation_cw`` (set
        # by ``VideoArea`` at load time).  When that information is available
        # we use it *instead of* Qt's rotation, combined with a dimension
        # comparison to detect whether the multimedia backend already
        # pre-rotated the decoded frames:
        #
        #   • frame dims match raw stream dims  → NOT pre-rotated → apply
        #   • frame dims match *rotated* raw    → pre-rotated     → skip
        #
        # When ffprobe metadata is unavailable we fall back to Qt's value.
        container_rot = self._container_rotation_cw
        raw_w = self._container_raw_w
        raw_h = self._container_raw_h

        if container_rot != 0:
            # ffprobe rotation available — use it as the primary source.
            pre_rotated = False

            if container_rot in (90, 270) and raw_w > 0 and raw_h > 0:
                # 90°/270° rotation swaps width and height; use that to
                # detect whether the backend already applied the rotation.
                # Pre-rotation detection requires raw coded dimensions.
                if w == raw_h and h == raw_w:
                    pre_rotated = True
                elif w != raw_w or h != raw_h:
                    # Dimensions don't match exactly (e.g. slight crop or
                    # scaling by the decoder).  Compare aspect ratios.
                    frame_ar = w / h if h > 0 else 0.0
                    raw_ar = raw_w / raw_h if raw_h > 0 else 0.0
                    rotated_ar = raw_h / raw_w if raw_w > 0 else 0.0
                    # If the frame matches the *rotated* aspect but NOT the
                    # original, the backend pre-rotated.  For nearly-square
                    # videos both ratios are close; prefer "not pre-rotated".
                    if (
                        rotated_ar > 0
                        and abs(frame_ar - rotated_ar) < 0.05
                        and (raw_ar <= 0 or abs(frame_ar - raw_ar) >= 0.05)
                    ):
                        pre_rotated = True
            elif container_rot == 180:
                # 180° rotation keeps width/height unchanged, so dimension
                # checks cannot detect pre-rotated frames.  On Linux backends
                # (FFmpeg/GStreamer), some clips are already decoded upright
                # while rotation metadata is still reported, which causes a
                # double 180° flip if we apply container rotation again.
                #
                # Heuristic: only when Qt reports 180° *and* the environment
                # indicates a Linux backend known to pre-rotate pixels (or an
                # explicit opt-in override), treat the frame as pre-rotated
                # and skip the extra transform.
                qt_rot = 0
                rotation = fmt.rotation()
                try:
                    qt_rot = rotation.value if hasattr(rotation, "value") else int(rotation)
                except (TypeError, ValueError):
                    qt_rot = 0
                if (
                    abs(qt_rot) % 360 == 180
                    and _should_assume_linux_180_prerotated(self._container_linux_180_hint)
                ):
                    pre_rotated = True

            rot_deg = 0 if pre_rotated else container_rot
        elif raw_w > 0:
            # ffprobe ran successfully but reported 0° — trust it; do not
            # fall back to Qt which may report a different value.
            rot_deg = 0
        else:
            # No ffprobe data at all — fall back to Qt's value.
            rotation = fmt.rotation()
            try:
                rot_deg = rotation.value if hasattr(rotation, "value") else int(rotation)
            except (TypeError, ValueError):
                rot_deg = 0
        # ---- end rotation resolution ----------------------------------------

        self._rotate90_steps = (rot_deg // 90) % 4
        self._mirror = 1 if fmt.isMirrored() else 0

        # Compute the *display* native size: for 90°/270° rotations the
        # width and height are swapped so that the aspect-ratio letterbox
        # calculation uses the orientation the user sees.
        if self._rotate90_steps in (1, 3):
            display_w, display_h = h, w
        else:
            display_w, display_h = w, h

        new_size = QSizeF(display_w, display_h)
        if new_size != self._native_size:
            self._native_size = new_size
            self.nativeSizeChanged.emit(new_size)

        # Classify frame metadata
        self._fmt_enum, self._cs_enum, self._tf_enum, self._range_enum = (
            _classify_frame_format(fmt)
        )

        self.update()

    def clear_frame(self) -> None:
        """Clear the current frame and repaint with letterbox only."""
        self._current_frame = None
        self._frame_dirty = False
        self._native_size = QSizeF()
        self._rotate90_steps = 0
        self._mirror = 0
        self._container_rotation_cw = 0
        self._container_raw_w = 0
        self._container_raw_h = 0
        self._container_linux_180_hint = False
        self._has_frame = False
        # Reset tracked texture formats so that the next video always
        # recreates textures with the correct format, even when the
        # resolution is identical (e.g. switching between an 8-bit NV12
        # and a 10-bit P010 stream at the same frame size).
        self._tex_y_fmt = None
        self._tex_uv_fmt = None
        self.update()

    def set_letterbox_color(self, color: QColor) -> None:
        """Set the colour used for letterbox/pillarbox areas."""
        self._letterbox_color = QColor(color)
        self.update()

    def native_size(self) -> QSizeF:
        """Return the native resolution of the current video frame."""
        return self._native_size

    # ------------------------------------------------------------------
    # QRhiWidget overrides
    # ------------------------------------------------------------------
    def initialize(self, cb) -> None:  # type: ignore[override]
        """Create GPU resources the first time the widget is rendered."""
        if self._initialized:
            return

        rhi = self.rhi()
        if rhi is None:
            _log.warning("QRhi not available — video rendering disabled")
            return

        # --- Load shaders ---
        try:
            vert_shader = _load_shader(_VERT_QSB)
            frag_shader = _load_shader(_FRAG_QSB)
        except Exception:
            _log.exception("Failed to load video renderer shaders")
            return

        # --- Vertex buffer (full-screen quad) ---
        # Two triangles covering clip space [-1, 1], UV [0, 1]
        #   position (x, y), texcoord (u, v)
        vertices = [
            # Triangle 1
            -1.0, -1.0,  0.0, 1.0,
            1.0,  -1.0,  1.0, 1.0,
            -1.0,  1.0,  0.0, 0.0,
            # Triangle 2
            1.0,  -1.0,  1.0, 1.0,
            1.0,   1.0,  1.0, 0.0,
            -1.0,  1.0,  0.0, 0.0,
        ]
        vdata = struct.pack(f"{len(vertices)}f", *vertices)

        self._vbuf = rhi.newBuffer(
            QRhiBuffer.Type.Immutable,
            QRhiBuffer.UsageFlag.VertexBuffer,
            len(vdata),
        )
        self._vbuf.create()

        # --- Uniform buffer ---
        self._ubuf = rhi.newBuffer(
            QRhiBuffer.Type.Dynamic,
            QRhiBuffer.UsageFlag.UniformBuffer,
            _UBO_SIZE,
        )
        self._ubuf.create()

        # --- Sampler ---
        self._sampler = rhi.newSampler(
            QRhiSampler.Filter.Linear,
            QRhiSampler.Filter.Linear,
            QRhiSampler.Filter.None_,
            QRhiSampler.AddressMode.ClampToEdge,
            QRhiSampler.AddressMode.ClampToEdge,
        )
        self._sampler.create()

        # --- Placeholder textures ---
        self._tex_y = self._create_placeholder_texture(rhi, 2, 2, QRhiTexture.Format.R8)
        self._tex_uv = self._create_placeholder_texture(rhi, 1, 1, QRhiTexture.Format.RG8)
        self._tex_rgba = self._create_placeholder_texture(rhi, 2, 2, QRhiTexture.Format.RGBA8)

        # --- Shader resource bindings ---
        self._srb = rhi.newShaderResourceBindings()
        self._srb.setBindings([
            QRhiShaderResourceBinding.uniformBuffer(
                0,
                QRhiShaderResourceBinding.StageFlag.FragmentStage,
                self._ubuf,
            ),
            QRhiShaderResourceBinding.sampledTexture(
                1,
                QRhiShaderResourceBinding.StageFlag.FragmentStage,
                self._tex_y,
                self._sampler,
            ),
            QRhiShaderResourceBinding.sampledTexture(
                2,
                QRhiShaderResourceBinding.StageFlag.FragmentStage,
                self._tex_uv,
                self._sampler,
            ),
            QRhiShaderResourceBinding.sampledTexture(
                3,
                QRhiShaderResourceBinding.StageFlag.FragmentStage,
                self._tex_rgba,
                self._sampler,
            ),
        ])
        self._srb.create()

        # --- Graphics pipeline ---
        self._pipeline = rhi.newGraphicsPipeline()

        self._pipeline.setShaderStages([
            QRhiShaderStage(QRhiShaderStage.Type.Vertex, vert_shader),
            QRhiShaderStage(QRhiShaderStage.Type.Fragment, frag_shader),
        ])

        # Disable alpha blending — the video surface is always fully opaque.
        target_blend = QRhiGraphicsPipeline.TargetBlend()
        target_blend.enable = False

        self._pipeline.setTargetBlends([target_blend])
        self._pipeline.setShaderResourceBindings(self._srb)
        self._pipeline.setRenderPassDescriptor(self.renderTarget().renderPassDescriptor())

        # Define vertex input
        binding_desc = QRhiVertexInputBinding(4 * 4)  # stride = 16 bytes
        attr_pos = QRhiVertexInputAttribute(
            0, 0, QRhiVertexInputAttribute.Format.Float2, 0
        )
        attr_uv = QRhiVertexInputAttribute(
            0, 1, QRhiVertexInputAttribute.Format.Float2, 2 * 4
        )
        input_layout_desc = QRhiVertexInputLayout()
        input_layout_desc.setBindings([binding_desc])
        input_layout_desc.setAttributes([attr_pos, attr_uv])
        self._pipeline.setVertexInputLayout(input_layout_desc)

        self._pipeline.create()

        # Upload vertex data
        ru = rhi.nextResourceUpdateBatch()
        ru.uploadStaticBuffer(self._vbuf, vdata)
        cb.resourceUpdate(ru)

        self._initialized = True
        _log.debug("VideoRendererWidget: RHI resources initialised")

    def render(self, cb) -> None:  # type: ignore[override]
        """Render the current video frame (or letterbox if no frame is present)."""
        if not self._initialized:
            # GPU pipeline not yet ready but we MUST still clear the render
            # target with an opaque colour so the surface is never transparent.
            lc = self._letterbox_color
            cb.beginPass(
                self.renderTarget(),
                QColor.fromRgbF(lc.redF(), lc.greenF(), lc.blueF(), 1.0),
                QRhiDepthStencilClearValue(),
            )
            cb.endPass()
            self._emit_first_frame_ready()
            return

        rhi = self.rhi()
        if rhi is None:
            return

        output_size = self.renderTarget().pixelSize()
        if output_size.isEmpty():
            return

        # When no video frame has been loaded (or after clear_frame()), fill
        # the render target with the opaque letterbox colour.  This prevents
        # stale texture data from a previously played video from flashing on
        # screen during media transitions (video→video or video→image).
        if not self._has_frame:
            lc = self._letterbox_color
            cb.beginPass(
                self.renderTarget(),
                QColor.fromRgbF(lc.redF(), lc.greenF(), lc.blueF(), 1.0),
                QRhiDepthStencilClearValue(),
            )
            cb.endPass()
            self._emit_first_frame_ready()
            return

        ru = rhi.nextResourceUpdateBatch()

        # Upload frame data if dirty.
        # Only clear the dirty flag *after* the upload succeeds so that a
        # failed map/upload attempt is retried on the next render cycle
        # instead of leaving uninitialized textures on screen.
        if self._frame_dirty:
            if self._upload_frame(rhi, ru):
                self._frame_dirty = False
                # Release the decoded frame reference immediately so the
                # hardware decoder can recycle its buffer.  All pixel data
                # has already been copied into GPU textures.
                self._current_frame = None

        # Update uniform buffer
        self._update_uniforms(ru, output_size)

        cb.resourceUpdate(ru)

        # Draw
        cb.beginPass(self.renderTarget(), QColor(0, 0, 0, 255), QRhiDepthStencilClearValue())
        cb.setGraphicsPipeline(self._pipeline)
        cb.setShaderResources(self._srb)
        cb.setViewport(QRhiViewport(0, 0, output_size.width(), output_size.height()))
        vbuf_binding = [(self._vbuf, 0)]
        cb.setVertexInput(0, vbuf_binding)
        cb.draw(6)  # 6 vertices = 2 triangles
        cb.endPass()
        self._emit_first_frame_ready()

    def _emit_first_frame_ready(self) -> None:
        """Notify listeners that the first opaque frame has been rendered."""
        if not self._first_render_done:
            self._first_render_done = True
            self.firstFrameReady.emit()

    def releaseResources(self) -> None:  # type: ignore[override]
        """Clean up GPU resources."""
        self._initialized = False
        # Qt will clean up RHI resources when the widget is destroyed

    # ------------------------------------------------------------------
    # Frame upload helpers
    # ------------------------------------------------------------------
    def _upload_frame(self, rhi: QRhi, ru) -> bool:
        """Upload the current video frame data to GPU textures.

        Returns ``True`` if the upload succeeded.  A return of ``False``
        means the frame should be retried on the next render cycle.
        """
        frame = self._current_frame
        if frame is None:
            return False

        if not frame.isValid():
            return False

        fmt = frame.surfaceFormat()
        pf = fmt.pixelFormat()

        if pf in (
            QVideoFrameFormat.PixelFormat.Format_NV12,
            QVideoFrameFormat.PixelFormat.Format_P010,
        ):
            ok = self._upload_nv12_frame(rhi, ru, frame, pf)
            if not ok:
                # Hardware-decoded NV12/P010 frames may occasionally fail to
                # map (e.g. decoder buffer recycling, GPU fence timeout).
                # Fall back to Qt's built-in toImage() conversion which
                # handles platform quirks internally.
                _log.debug("NV12/P010 upload failed – falling back to RGBA path")
                return self._upload_rgba_frame(rhi, ru, frame)
            return True
        else:
            return self._upload_rgba_frame(rhi, ru, frame)

    def _upload_nv12_frame(self, rhi: QRhi, ru, frame: "QVideoFrame", pf) -> bool:
        """Upload NV12/P010 frame planes as Y + UV textures.

        Returns ``True`` on success.
        """
        if not frame.map(QVideoFrame.MapMode.ReadOnly):
            _log.debug("Failed to map video frame for reading")
            return False

        try:
            fmt = frame.surfaceFormat()
            w = fmt.frameWidth()
            h = fmt.frameHeight()

            if w <= 0 or h <= 0:
                _log.debug("Frame has invalid dimensions: %dx%d", w, h)
                return False

            # Validate that we have at least 2 planes (Y + UV)
            plane_count = frame.planeCount()
            if plane_count < 2:
                _log.debug(
                    "NV12/P010 frame has only %d planes (need 2)", plane_count
                )
                return False

            # Y plane
            y_format = QRhiTexture.Format.R8
            if pf == QVideoFrameFormat.PixelFormat.Format_P010:
                y_format = QRhiTexture.Format.R16

            # Recreate Y texture if size or format changed.
            # Both dimensions AND format must match; switching between an
            # 8-bit (NV12 → R8) and 10-bit (P010 → R16) stream at the same
            # resolution would otherwise reuse a texture with the wrong
            # format, causing garbled frames or GPU crashes.
            if (self._tex_y is None or
                    self._tex_y.pixelSize().width() != w or
                    self._tex_y.pixelSize().height() != h or
                    self._tex_y_fmt != y_format):
                if self._tex_y is not None:
                    self._tex_y.destroy()
                self._tex_y = rhi.newTexture(y_format, QSize(w, h))
                self._tex_y.create()
                self._tex_y_fmt = y_format
                self._rebuild_srb(rhi)

            # UV plane (half resolution, 2 channels)
            uv_w = w // 2
            uv_h = h // 2
            uv_format = QRhiTexture.Format.RG8
            if pf == QVideoFrameFormat.PixelFormat.Format_P010:
                uv_format = QRhiTexture.Format.RG16

            if (self._tex_uv is None or
                    self._tex_uv.pixelSize().width() != uv_w or
                    self._tex_uv.pixelSize().height() != uv_h or
                    self._tex_uv_fmt != uv_format):
                if self._tex_uv is not None:
                    self._tex_uv.destroy()
                self._tex_uv = rhi.newTexture(uv_format, QSize(uv_w, uv_h))
                self._tex_uv.create()
                self._tex_uv_fmt = uv_format
                self._rebuild_srb(rhi)

            # Upload Y plane
            y_bytes_per_line = frame.bytesPerLine(0)
            y_data_ptr = frame.bits(0)
            if y_bytes_per_line <= 0 or y_data_ptr is None:
                _log.debug("Y plane has invalid stride or null data pointer")
                return False
            y_data_size = y_bytes_per_line * h
            y_data = bytes(y_data_ptr[:y_data_size])

            y_sub = QRhiTextureSubresourceUploadDescription(y_data)
            y_sub.setDataStride(y_bytes_per_line)
            y_upload = QRhiTextureUploadDescription(
                QRhiTextureUploadEntry(0, 0, y_sub)
            )
            ru.uploadTexture(self._tex_y, y_upload)

            # Upload UV plane
            uv_bytes_per_line = frame.bytesPerLine(1)
            uv_data_ptr = frame.bits(1)
            if uv_bytes_per_line <= 0 or uv_data_ptr is None:
                _log.debug("UV plane has invalid stride or null data pointer")
                return False
            uv_data_size = uv_bytes_per_line * uv_h
            uv_data = bytes(uv_data_ptr[:uv_data_size])

            uv_sub = QRhiTextureSubresourceUploadDescription(uv_data)
            uv_sub.setDataStride(uv_bytes_per_line)
            uv_upload = QRhiTextureUploadDescription(
                QRhiTextureUploadEntry(0, 0, uv_sub)
            )
            ru.uploadTexture(self._tex_uv, uv_upload)

            return True

        finally:
            frame.unmap()

    def _upload_rgba_frame(self, rhi: QRhi, ru, frame: "QVideoFrame") -> bool:
        """Convert frame to RGBA QImage and upload as a single texture.

        Returns ``True`` on success.
        """
        img = frame.toImage()
        if img.isNull():
            return False

        # Ensure RGBA8888 format
        if img.format() != QImage.Format.Format_RGBA8888:
            img = img.convertToFormat(QImage.Format.Format_RGBA8888)

        w = img.width()
        h = img.height()

        # On some Qt versions ``toImage()`` applies the frame's rotation
        # internally, producing an image whose dimensions are the transpose
        # of the surface format's ``frameWidth``/``frameHeight``.  We always
        # compare the image size against the surface format, regardless of
        # the current ``_rotate90_steps`` value.
        fmt = frame.surfaceFormat()
        fmt_w = fmt.frameWidth()
        fmt_h = fmt.frameHeight()
        if fmt_w > 0 and fmt_h > 0 and w == fmt_h and h == fmt_w:
            self._rotate90_steps = 0
            self._mirror = 0
            # Update the display native size to match the (now pre-rotated)
            # image dimensions so the aspect-ratio letterbox is correct.
            new_size = QSizeF(w, h)
            if new_size != self._native_size:
                self._native_size = new_size
                self.nativeSizeChanged.emit(new_size)

        if (self._tex_rgba is None or
                self._tex_rgba.pixelSize().width() != w or
                self._tex_rgba.pixelSize().height() != h):
            if self._tex_rgba is not None:
                self._tex_rgba.destroy()
            self._tex_rgba = rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(w, h))
            self._tex_rgba.create()
            self._rebuild_srb(rhi)

        # Set format to RGBA passthrough for shader
        self._fmt_enum = _FMT_RGBA

        rgba_sub = QRhiTextureSubresourceUploadDescription(img)
        rgba_upload = QRhiTextureUploadDescription(
            QRhiTextureUploadEntry(0, 0, rgba_sub)
        )
        ru.uploadTexture(self._tex_rgba, rgba_upload)
        return True

    # ------------------------------------------------------------------
    # Uniform update
    # ------------------------------------------------------------------
    def _update_uniforms(self, ru, output_size: QSize) -> None:
        """Pack and upload the uniform buffer."""
        ow = float(output_size.width())
        oh = float(output_size.height())

        # Compute video rect (either contain/letterbox or cover/crop).
        vx, vy, vw, vh = 0.0, 0.0, 1.0, 1.0
        if not self._native_size.isEmpty() and ow > 0 and oh > 0:
            src_aspect = self._native_size.width() / self._native_size.height()
            dst_aspect = ow / oh
            if self._viewport_fill_enabled:
                if src_aspect > dst_aspect:
                    vh = 1.0
                    vw = src_aspect / dst_aspect
                    vx = (1.0 - vw) / 2.0
                    vy = 0.0
                else:
                    vw = 1.0
                    vh = dst_aspect / src_aspect
                    vx = 0.0
                    vy = (1.0 - vh) / 2.0
            else:
                if src_aspect > dst_aspect:
                    # Wider than viewport → pillarbox
                    scale = ow / self._native_size.width()
                    vw = 1.0
                    vh = (self._native_size.height() * scale) / oh
                    vx = 0.0
                    vy = (1.0 - vh) / 2.0
                else:
                    # Taller than viewport → letterbox
                    scale = oh / self._native_size.height()
                    vh = 1.0
                    vw = (self._native_size.width() * scale) / ow
                    vx = (1.0 - vw) / 2.0
                    vy = 0.0

        # Letterbox color
        lc = self._letterbox_color
        lr = lc.redF()
        lg = lc.greenF()
        lb = lc.blueF()
        la = 1.0

        # Pack uniform data (std140)
        ubo_data = struct.pack(
            "iiii4f4fiiii",
            self._fmt_enum,
            self._cs_enum,
            self._tf_enum,
            self._range_enum,
            lr, lg, lb, la,  # u_letterbox_color
            vx, vy, vw, vh,  # u_video_rect
            self._rotate90_steps,  # u_rotate90
            self._mirror,          # u_mirror
            0,                     # _pad0
            0,                     # _pad1
        )

        ru.updateDynamicBuffer(self._ubuf, 0, len(ubo_data), ubo_data)

    # ------------------------------------------------------------------
    # Resource management helpers
    # ------------------------------------------------------------------
    def _create_placeholder_texture(
        self, rhi: QRhi, w: int, h: int, fmt: QRhiTexture.Format
    ) -> QRhiTexture:
        """Create a tiny placeholder texture to avoid null bindings."""
        tex = rhi.newTexture(fmt, QSize(w, h))
        tex.create()
        return tex

    def _rebuild_srb(self, rhi: QRhi) -> None:
        """Rebuild shader resource bindings after texture replacement."""
        if self._srb is None:
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
                self._tex_y,
                self._sampler,
            ),
            QRhiShaderResourceBinding.sampledTexture(
                2,
                QRhiShaderResourceBinding.StageFlag.FragmentStage,
                self._tex_uv,
                self._sampler,
            ),
            QRhiShaderResourceBinding.sampledTexture(
                3,
                QRhiShaderResourceBinding.StageFlag.FragmentStage,
                self._tex_rgba,
                self._sampler,
            ),
        ])
        self._srb.create()
