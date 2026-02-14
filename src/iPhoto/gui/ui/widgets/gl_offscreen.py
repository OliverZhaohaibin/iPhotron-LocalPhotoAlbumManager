# -*- coding: utf-8 -*-
"""Off-screen rendering helper for the GL renderer."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Mapping

from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import QImage
from PySide6.QtOpenGL import (
    QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat,
)
from OpenGL import GL as gl

if TYPE_CHECKING:
    from .gl_renderer import GLRenderer

_LOGGER = logging.getLogger(__name__)


def render_offscreen_image(
    renderer: GLRenderer,
    image: QImage,
    adjustments: Mapping[str, float],
    target_size: QSize,
    time_base: float = 0.0,
) -> QImage:
    """Render *image* into an off-screen framebuffer and return a :class:`QImage`.

    Parameters
    ----------
    renderer:
        The :class:`GLRenderer` instance that owns the GPU resources.
    image:
        Source image to render.
    adjustments:
        Mapping of shader uniform values to apply during rendering.
    target_size:
        Final size of the rendered preview.  Width and height are clamped
        to at least one pixel to avoid driver errors.
    time_base:
        Time base for animated effects (default: 0.0).

    Returns
    -------
    QImage
        CPU-side image containing the rendered frame, converted to
        Format_ARGB32.
    """
    if target_size.isEmpty():
        _LOGGER.warning("render_offscreen_image: target size was empty")
        return QImage()

    if renderer._gl_funcs is None:
        _LOGGER.error("render_offscreen_image: renderer not initialized")
        return QImage()

    if not renderer.has_texture():
        renderer.upload_texture(image)
    if not renderer.has_texture():
        _LOGGER.error("render_offscreen_image: texture upload failed")
        return QImage()

    gf = renderer._gl_funcs
    width = max(1, int(target_size.width()))
    height = max(1, int(target_size.height()))

    previous_fbo = gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING)
    previous_viewport = gl.glGetIntegerv(gl.GL_VIEWPORT)

    fbo_format = QOpenGLFramebufferObjectFormat()
    fbo_format.setAttachment(QOpenGLFramebufferObject.CombinedDepthStencil)
    fbo_format.setTextureTarget(gl.GL_TEXTURE_2D)
    fbo = QOpenGLFramebufferObject(width, height, fbo_format)
    if not fbo.isValid():
        _LOGGER.error("render_offscreen_image: failed to allocate framebuffer object")
        return QImage()

    try:
        fbo.bind()
        gf.glViewport(0, 0, width, height)
        gf.glClearColor(0.0, 0.0, 0.0, 0.0)
        gf.glClear(gl.GL_COLOR_BUFFER_BIT)

        # Import here to avoid circular dependency
        from .view_transform_controller import compute_fit_to_view_scale

        texture_size = renderer.texture_size()
        tex_w, tex_h = texture_size
        rotate_steps = int(float(adjustments.get("Crop_Rotate90", 0.0)))
        if rotate_steps % 2 and tex_w > 0 and tex_h > 0:
            logical_tex_size = (tex_h, tex_w)
        else:
            logical_tex_size = texture_size

        base_scale = compute_fit_to_view_scale(logical_tex_size, float(width), float(height))
        effective_scale = max(base_scale, 1e-6)
        time_value = time.monotonic() - time_base

        renderer.render(
            view_width=float(width),
            view_height=float(height),
            scale=effective_scale,
            pan=QPointF(0.0, 0.0),
            adjustments=dict(adjustments),
            time_value=time_value,
            logical_tex_size=(float(logical_tex_size[0]), float(logical_tex_size[1])),
        )

        return fbo.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    finally:
        fbo.release()
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, previous_fbo)
        try:
            x, y, w, h = [int(v) for v in previous_viewport]
            gf.glViewport(x, y, w, h)
        except Exception:
            pass
