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
        self._current_cache_key: int | None = None
        self._texture_dirty = False
    
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
        *,
        force_upload: bool = False,
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
        old_cache_key = self._current_cache_key
        self._current_image_source = image_source
        self._current_image = image
        self._current_cache_key = None
        
        # Return whether we need a new upload
        if image is None or image.isNull():
            self._texture_dirty = False
            return old_source is not None  # Need to clear texture

        cache_key = int(image.cacheKey()) if hasattr(image, "cacheKey") else None
        self._current_cache_key = cache_key
        needs_upload = bool(
            force_upload
            or image_source is None
            or image_source != old_source
            or old_source is None
            or cache_key != old_cache_key
        )
        self._texture_dirty = needs_upload
        return needs_upload
    
    def clear_image(self) -> None:
        """Clear the current image and delete the GPU texture.
        
        This handles the GL context management for texture deletion.
        """
        self._current_image_source = None
        self._current_image = None
        self._current_cache_key = None
        self._texture_dirty = False
        
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

    def invalidate_texture(self) -> None:
        """Delete the currently bound GPU texture without forgetting the image state."""

        renderer = self._renderer_provider()
        if renderer is None or not renderer.has_texture():
            self._texture_dirty = self._current_image is not None and not self._current_image.isNull()
            return
        gl_context = self._context_provider()
        if gl_context is None:
            self._texture_dirty = self._current_image is not None and not self._current_image.isNull()
            return
        self._make_current()
        try:
            renderer.delete_texture()
        finally:
            self._done_current()
        self._texture_dirty = self._current_image is not None and not self._current_image.isNull()
    
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
        if not self._texture_dirty and renderer.has_texture():
            return False
        renderer.upload_texture(image)
        self._texture_dirty = False
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
        
        return self._texture_dirty or not renderer.has_texture()
