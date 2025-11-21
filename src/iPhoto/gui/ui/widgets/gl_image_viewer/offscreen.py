"""
Offscreen rendering helper for GL image viewer.

This module handles the export/screenshot functionality, managing GL context
switching and error handling for offscreen rendering operations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

if TYPE_CHECKING:
    from collections.abc import Mapping
    from ..gl_renderer import GLRenderer

_LOGGER = logging.getLogger(__name__)


class OffscreenRenderer:
    """Helper class for rendering images offscreen.
    
    This class encapsulates the logic for creating offscreen renders,
    managing GL context activation, and handling error conditions.
    """
    
    @staticmethod
    def render(
        renderer: GLRenderer | None,
        context,
        make_current: callable,
        done_current: callable,
        image: QImage | None,
        adjustments: Mapping[str, float],
        target_size: QSize,
        time_base: float,
    ) -> QImage:
        """Render the current texture into an off-screen framebuffer.
        
        Parameters
        ----------
        renderer:
            The GLRenderer instance to use for rendering
        context:
            The OpenGL context
        make_current:
            Callable to make the GL context current
        done_current:
            Callable to release the GL context
        image:
            Source image to render
        adjustments:
            Mapping of shader uniform values to apply during rendering
        target_size:
            Final size of the rendered preview
        time_base:
            Time base for shader animations
            
        Returns
        -------
        QImage
            CPU-side image containing the rendered frame in Format_ARGB32
        """
        # Validate inputs
        if target_size.isEmpty():
            _LOGGER.warning("render_offscreen_image: target size was empty")
            return QImage()
        
        if context is None:
            _LOGGER.warning("render_offscreen_image: no OpenGL context available")
            return QImage()
        
        if image is None or image.isNull():
            _LOGGER.warning("render_offscreen_image: no source image bound to the viewer")
            return QImage()
        
        if renderer is None:
            _LOGGER.warning("render_offscreen_image: renderer not initialized")
            return QImage()
        
        # Perform offscreen render with proper context management
        make_current()
        try:
            return renderer.render_offscreen_image(
                image,
                adjustments,
                target_size,
                time_base=time_base,
            )
        finally:
            done_current()
        
