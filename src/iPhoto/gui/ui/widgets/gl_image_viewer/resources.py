"""
Texture resource management for GL image viewer.

This module handles the lifecycle of texture resources, including tracking
image sources, managing texture uploads, and cleaning up GL resources.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtGui import QImage

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


class TextureResourceManager:
    """Manages texture resource lifecycle for the GL image viewer.
    
    This class tracks the current image source, determines when texture
    re-uploads are needed, and coordinates with the renderer for texture
    creation and deletion.
    
    Parameters
    ----------
    renderer_provider:
        Callable that returns the current GLRenderer instance
    context_provider:
        Callable that returns the current OpenGL context
    make_current:
        Callable to make the GL context current
    done_current:
        Callable to release the GL context
    """
    
    def __init__(
        self,
        renderer_provider: callable,
        context_provider: callable,
        make_current: callable,
        done_current: callable,
    ) -> None:
        self._renderer_provider = renderer_provider
        self._context_provider = context_provider
        self._make_current = make_current
        self._done_current = done_current
        
        self._current_image_source: object | None = None
        self._current_image: QImage | None = None
    
    def get_current_image_source(self) -> object | None:
        """Return the identifier of the currently loaded image source."""
        return self._current_image_source
    
    def get_current_image(self) -> QImage | None:
        """Return the currently loaded image."""
        return self._current_image
    
    def should_reuse_texture(self, new_source: object | None) -> bool:
        """Determine if the existing texture can be reused.
        
        Parameters
        ----------
        new_source:
            The image source identifier for the new image
            
        Returns
        -------
        bool
            True if the texture can be reused (source matches current)
        """
        return (
            new_source is not None
            and new_source == self._current_image_source
        )
    
    def set_image(
        self,
        image: QImage | None,
        image_source: object | None,
    ) -> bool:
        """Update the current image and source.
        
        This method updates internal tracking but does NOT upload to GPU.
        Call upload_texture_if_needed() separately to perform the upload.
        
        Parameters
        ----------
        image:
            The new image to track
        image_source:
            Identifier for the image source
            
        Returns
        -------
        bool
            True if this is a new image that needs uploading
        """
        old_source = self._current_image_source
        self._current_image_source = image_source
        self._current_image = image
        
        # Return whether we need a new upload
        if image is None or image.isNull():
            return old_source is not None  # Need to clear texture
        
        # Need upload if source changed or this is first image
        return image_source != old_source or old_source is None
    
    def clear_image(self) -> None:
        """Clear the current image and delete the GPU texture.
        
        This handles the GL context management for texture deletion.
        """
        self._current_image_source = None
        self._current_image = None
        
        renderer = self._renderer_provider()
        if renderer is not None:
            gl_context = self._context_provider()
            if gl_context is not None:
                # Only touch GPU state when context is available
                self._make_current()
                try:
                    renderer.delete_texture()
                finally:
                    self._done_current()
    
    def upload_texture_if_needed(self, image: QImage) -> bool:
        """Upload texture to GPU if the image is valid.
        
        Parameters
        ----------
        image:
            The image to upload
            
        Returns
        -------
        bool
            True if upload was performed, False if skipped
        """
        if image is None or image.isNull():
            return False
        
        renderer = self._renderer_provider()
        if renderer is None:
            return False
        
        # Check if renderer already has this texture
        if renderer.has_texture():
            return False
        
        # Upload is handled by the renderer during paintGL
        # This method is mainly for tracking state
        return True
    
    def needs_texture_upload(self) -> bool:
        """Check if current image needs to be uploaded to GPU.
        
        Returns
        -------
        bool
            True if there's an image that hasn't been uploaded yet
        """
        renderer = self._renderer_provider()
        if renderer is None:
            return False
        
        if self._current_image is None or self._current_image.isNull():
            return False
        
        return not renderer.has_texture()
