"""Gallery page embedding the grid view inside a simple layout."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QVBoxLayout, QWidget

from ..icon import load_icon
from .gallery_grid_view import GalleryGridView


class GalleryPageWidget(QWidget):
    """Thin wrapper that exposes the gallery grid view as a self-contained page."""

    backRequested = Signal()
    """Signal emitted when the back button is clicked in cluster gallery mode."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("galleryPage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header with optional back button for cluster gallery mode
        self._header = QWidget()
        self._header.setObjectName("galleryHeader")
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(8, 8, 8, 8)
        header_layout.setSpacing(8)

        self.back_button = QToolButton()
        self.back_button.setObjectName("galleryBackButton")
        self.back_button.setIcon(load_icon("chevron.left.svg"))
        self.back_button.setToolTip("Return to Map")
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.clicked.connect(self.backRequested.emit)
        header_layout.addWidget(self.back_button)
        header_layout.addStretch()

        # Hide header by default; shown only in cluster gallery mode
        self._header.hide()
        layout.addWidget(self._header)

        self.grid_view = GalleryGridView()
        self.grid_view.setObjectName("galleryGridView")
        layout.addWidget(self.grid_view)

    def set_cluster_gallery_mode(self, enabled: bool) -> None:
        """Show or hide the header with back button for cluster gallery mode.

        Args:
            enabled: True to show the back button header (cluster gallery mode),
                     False to hide it (normal gallery mode).
        """
        self._header.setVisible(enabled)


__all__ = ["GalleryPageWidget"]
