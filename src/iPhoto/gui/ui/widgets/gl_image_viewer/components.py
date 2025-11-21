"""
UI components for GL image viewer.

This module contains reusable UI components like loading overlays
that are displayed on top of the GL viewport.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QLabel, QWidget


class LoadingOverlay:
    """Loading overlay component for GL image viewer.
    
    Manages a semi-transparent loading indicator that appears over
    the GL viewport during loading operations.
    
    Parameters
    ----------
    parent:
        Parent widget for the overlay label
    """
    
    def __init__(self, parent: QWidget) -> None:
        self._overlay = QLabel("Loading…", parent)
        self._overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self._overlay.setStyleSheet(
            "background-color: rgba(0, 0, 0, 128); color: white; font-size: 18px;"
        )
        self._overlay.hide()
    
    def show(self) -> None:
        """Display the loading overlay."""
        self._overlay.setVisible(True)
        self._overlay.raise_()
    
    def hide(self) -> None:
        """Hide the loading overlay."""
        self._overlay.hide()
    
    def update_geometry(self, size: QSize) -> None:
        """Update overlay size to match parent.
        
        Parameters
        ----------
        size:
            New size for the overlay
        """
        self._overlay.resize(size)
    
    def is_visible(self) -> bool:
        """Check if overlay is currently visible.
        
        Returns
        -------
        bool
            True if overlay is visible
        """
        return self._overlay.isVisible()
