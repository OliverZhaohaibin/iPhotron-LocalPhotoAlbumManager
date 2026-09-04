"""
GL Image Viewer package.

This package provides a GPU-accelerated image viewer with support for
pixel-accurate zoom, pan, rotation, and cropping operations.

The main entry point is the GLImageViewer class, which maintains backward
compatibility with the original single-file implementation.
"""

from .widget import GLImageViewer, preload_opengl_python_runtime

__all__ = ["GLImageViewer", "preload_opengl_python_runtime"]
