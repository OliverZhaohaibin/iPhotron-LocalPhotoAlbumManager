"""QML-based gallery page with explicit rendering pipeline configuration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Property,
    QModelIndex,
    QMetaObject,
    QObject,
    QSize,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QPalette,
    QPixmap,
    QSurfaceFormat,
)
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QVBoxLayout, QWidget

if TYPE_CHECKING:  # pragma: no cover
    from ..models.asset_list.model import AssetListModel

logger = logging.getLogger(__name__)


class ThumbnailImageProvider(QQuickImageProvider):
    """Provide thumbnails to QML using the existing thumbnail loader infrastructure.

    This provider bridges the Qt Quick image loading system with the application's
    existing thumbnail caching and loading infrastructure.
    """

    def __init__(self, cache_manager=None) -> None:
        """Initialize the provider.

        Args:
            cache_manager: The AssetCacheManager instance for thumbnail retrieval.
        """
        super().__init__(QQuickImageProvider.ImageType.Pixmap)
        self._cache_manager = cache_manager
        self._fallback_pixmap: QPixmap | None = None

    def set_cache_manager(self, cache_manager) -> None:
        """Update the cache manager reference."""
        self._cache_manager = cache_manager

    def requestPixmap(
        self, id: str, size: QSize, requestedSize: QSize
    ) -> QPixmap:
        """Return the thumbnail pixmap for the given relative path.

        This method is called by the QML engine when an Image element requests
        a pixmap from this provider.

        Args:
            id: The relative path of the asset (used as cache key).
            size: Unused by this implementation (Qt Quick provides this for
                image providers that need to report actual dimensions).
            requestedSize: The requested size from QML.

        Returns:
            The cached thumbnail pixmap or a placeholder.
        """
        _ = size  # Unused, Qt Quick image provider interface requirement
        # Decode the path (QML URL-encodes special characters)
        rel_path = id

        if self._cache_manager is None:
            return self._create_placeholder(requestedSize)

        # Try to get from cache
        pixmap = self._cache_manager.thumbnail_for(rel_path)
        if pixmap is not None and not pixmap.isNull():
            return pixmap

        # Return placeholder and trigger background load if needed
        return self._create_placeholder(requestedSize)

    def _create_placeholder(self, size: QSize) -> QPixmap:
        """Create a dark placeholder pixmap."""
        target_size = size if size.isValid() else QSize(192, 192)
        if self._fallback_pixmap is None or self._fallback_pixmap.size() != target_size:
            self._fallback_pixmap = QPixmap(target_size)
            self._fallback_pixmap.fill(QColor("#1b1b1b"))
        return self._fallback_pixmap


class QmlGalleryBridge(QObject):
    """Bridge object exposed to QML for handling gallery interactions."""

    itemClicked = Signal(int)
    itemDoubleClicked = Signal(int)
    requestPreview = Signal(int)
    previewReleased = Signal()
    previewCancelled = Signal()
    visibleRowsChanged = Signal(int, int)

    selectionModeChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._selection_mode_enabled = False

    @Property(bool, notify=selectionModeChanged)
    def selectionModeEnabled(self) -> bool:
        """Return whether selection mode is active."""
        return self._selection_mode_enabled

    @selectionModeEnabled.setter
    def selectionModeEnabled(self, enabled: bool) -> None:
        """Set the selection mode state."""
        if self._selection_mode_enabled != enabled:
            self._selection_mode_enabled = enabled
            self.selectionModeChanged.emit(enabled)

    @Slot(int)
    def onItemClicked(self, index: int) -> None:
        """Handle item click from QML."""
        self.itemClicked.emit(index)

    @Slot(int)
    def onItemDoubleClicked(self, index: int) -> None:
        """Handle item double-click from QML."""
        self.itemDoubleClicked.emit(index)

    @Slot(int)
    def onRequestPreview(self, index: int) -> None:
        """Handle preview request from QML."""
        self.requestPreview.emit(index)

    @Slot()
    def onPreviewReleased(self) -> None:
        """Handle preview release from QML."""
        self.previewReleased.emit()

    @Slot()
    def onPreviewCancelled(self) -> None:
        """Handle preview cancellation from QML."""
        self.previewCancelled.emit()

    @Slot(int, int)
    def onVisibleRowsChanged(self, first: int, last: int) -> None:
        """Handle visible rows change from QML."""
        self.visibleRowsChanged.emit(first, last)


class QmlGalleryGridView(QWidget):
    """Widget hosting the QML-based gallery grid view.

    This widget uses QQuickWidget with explicit surface format configuration
    to ensure opaque rendering when used with Windows frameless windows.
    The alpha buffer is disabled to prevent transparency issues with the
    Desktop Window Manager (DWM).
    """

    # Signals mirroring the original GalleryGridView
    itemClicked = Signal(QModelIndex)
    requestPreview = Signal(QModelIndex)
    previewReleased = Signal()
    previewCancelled = Signal()
    visibleRowsChanged = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model: AssetListModel | None = None
        self._selection_mode_enabled = False
        self._preview_enabled = True

        # Configure surface format to prevent transparency issues
        # This is critical for frameless windows on Windows
        surface_format = QSurfaceFormat()
        surface_format.setAlphaBufferSize(0)  # Disable alpha buffer
        surface_format.setDepthBufferSize(24)
        surface_format.setStencilBufferSize(8)
        surface_format.setVersion(3, 3)
        surface_format.setProfile(QSurfaceFormat.CoreProfile)
        surface_format.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)

        # Create the QQuickWidget
        self._quick_widget = QQuickWidget()
        self._quick_widget.setFormat(surface_format)

        # Configure rendering behavior
        # Use SizeRootObjectToView to make the QML root item fill the widget
        self._quick_widget.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)

        # Set the clear color to match the window background
        # This prevents any transparency artifacts
        self._quick_widget.setClearColor(QColor("#1b1b1b"))

        # Create the thumbnail image provider
        self._thumbnail_provider = ThumbnailImageProvider()

        # Create the bridge object for QML communication
        self._bridge = QmlGalleryBridge(self)
        self._connect_bridge_signals()

        # Add the image provider to the engine
        engine = self._quick_widget.engine()
        engine.addImageProvider("thumbnail", self._thumbnail_provider)

        # Expose the bridge to QML
        engine.rootContext().setContextProperty("galleryBridge", self._bridge)

        # Load the QML file
        qml_path = Path(__file__).parent.parent / "qml" / "GalleryGridView.qml"
        self._quick_widget.setSource(QUrl.fromLocalFile(str(qml_path)))

        # Check for QML loading errors
        if self._quick_widget.status() == QQuickWidget.Status.Error:
            errors = self._quick_widget.errors()
            for error in errors:
                logger.error("QML Error: %s", error.toString())

        # Set up the layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._quick_widget)

        # Apply initial styling
        self._apply_background_color()

    def _connect_bridge_signals(self) -> None:
        """Connect bridge signals to widget signals."""
        self._bridge.itemClicked.connect(self._on_item_clicked)
        self._bridge.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._bridge.requestPreview.connect(self._on_request_preview)
        self._bridge.previewReleased.connect(self.previewReleased)
        self._bridge.previewCancelled.connect(self.previewCancelled)
        self._bridge.visibleRowsChanged.connect(self.visibleRowsChanged)

    def _on_item_clicked(self, index: int) -> None:
        """Convert int index to QModelIndex and emit."""
        if self._model is not None:
            model_index = self._model.index(index, 0)
            self.itemClicked.emit(model_index)

    def _on_item_double_clicked(self, index: int) -> None:
        """Handle double-click (same as click for now)."""
        if self._model is not None:
            model_index = self._model.index(index, 0)
            self.itemClicked.emit(model_index)

    def _on_request_preview(self, index: int) -> None:
        """Convert int index to QModelIndex and emit preview request."""
        if not self._preview_enabled:
            return
        if self._model is not None:
            model_index = self._model.index(index, 0)
            self.requestPreview.emit(model_index)

    def _apply_background_color(self) -> None:
        """Apply the palette base color as the clear color."""
        base_color = QColor(self.palette().color(QPalette.ColorRole.Base))
        base_color.setAlpha(255)
        self._quick_widget.setClearColor(base_color)

        # Update the QML root item's color
        root = self._quick_widget.rootObject()
        if root is not None:
            root.setProperty("color", base_color)

    def changeEvent(self, event) -> None:
        """Handle palette changes to update the background color."""
        super().changeEvent(event)
        if event.type() == event.Type.PaletteChange:
            self._apply_background_color()

    # ------------------------------------------------------------------
    # Public API matching GalleryGridView
    # ------------------------------------------------------------------

    def setModel(self, model: AssetListModel) -> None:
        """Set the asset list model for the grid view."""
        self._model = model

        # Update the thumbnail provider's cache manager
        # Access the cache manager through the model's thumbnail_loader method
        # which provides a public interface to the internal caching infrastructure.
        try:
            cache_manager = getattr(model, "_cache_manager", None)
            if cache_manager is not None:
                self._thumbnail_provider.set_cache_manager(cache_manager)
        except AttributeError:
            # Model doesn't expose cache manager, thumbnails will use placeholders
            pass

        # Set the model on the QML root object
        root = self._quick_widget.rootObject()
        if root is not None:
            root.setProperty("assetModel", model)

        # Connect model signals to trigger thumbnail updates
        if model is not None:
            model.dataChanged.connect(self._on_data_changed)

    def model(self):
        """Return the current model."""
        return self._model

    def selectionModel(self):
        """Return the selection model (for compatibility)."""
        # QML handles selection internally, but we need this for compatibility
        return None

    def setItemDelegate(self, delegate) -> None:
        """Set the item delegate (no-op for QML, delegates are built-in)."""
        # QML handles item rendering natively

    def itemDelegate(self):
        """Return the item delegate (for compatibility)."""
        return None

    def viewport(self):
        """Return the viewport widget (the QQuickWidget itself)."""
        return self._quick_widget

    def iconSize(self) -> QSize:
        """Return the current icon size."""
        root = self._quick_widget.rootObject()
        if root is not None:
            item_size = root.property("itemSize")
            if item_size:
                return QSize(item_size, item_size)
        return QSize(192, 192)

    def setIconSize(self, size: QSize) -> None:
        """Set the icon size.

        Note: The QML implementation auto-calculates the icon size based on
        the viewport width and minimum item constraints to ensure optimal
        column layout. This method is provided for API compatibility with
        the original GalleryGridView but does not affect the QML layout.
        """
        # Icon size is computed dynamically in QML based on:
        # - viewport width
        # - minItemWidth (192px)
        # - itemGap (2px)
        # - safetyMargin (10px)

    def gridSize(self) -> QSize:
        """Return the current grid cell size."""
        root = self._quick_widget.rootObject()
        if root is not None:
            cell_size = root.property("cellSize")
            if cell_size:
                return QSize(cell_size, cell_size)
        return QSize(194, 194)

    def setGridSize(self, size: QSize) -> None:
        """Set the grid cell size (handled automatically by QML)."""

    # ------------------------------------------------------------------
    # Selection mode
    # ------------------------------------------------------------------

    def clearSelection(self) -> None:  # noqa: N802 - Qt API compatibility
        """Clear the current selection in the QML grid.

        The QML layer exposes a ``clearSelection`` helper; if that cannot be
        invoked, the method falls back to resetting the ``currentIndex`` on the
        GridView object directly.
        """
        root = self._quick_widget.rootObject()
        if root is None:
            return
        try:
            QMetaObject.invokeMethod(root, "clearSelection")
        except (RuntimeError, TypeError, AttributeError):
            grid_view = root.findChild(QObject, "gridView")
            if grid_view is not None:
                grid_view.setProperty("currentIndex", -1)

    def selection_mode_active(self) -> bool:
        """Return whether multi-selection mode is enabled."""
        return self._selection_mode_enabled

    def set_selection_mode_enabled(self, enabled: bool) -> None:
        """Toggle selection mode."""
        self._selection_mode_enabled = bool(enabled)
        self._bridge.selectionModeEnabled = enabled

    # ------------------------------------------------------------------
    # Preview mode
    # ------------------------------------------------------------------

    def set_preview_enabled(self, enabled: bool) -> None:
        """Enable or disable long-press preview."""
        self._preview_enabled = bool(enabled)

    def preview_enabled(self) -> bool:
        """Return whether previews are enabled."""
        return self._preview_enabled

    # ------------------------------------------------------------------
    # Thumbnail helpers
    # ------------------------------------------------------------------

    def _on_data_changed(
        self, top_left: QModelIndex, bottom_right: QModelIndex, roles: list
    ) -> None:
        """Handle model data changes by refreshing relevant QML items."""
        if Qt.ItemDataRole.DecorationRole in roles:
            # The QML will automatically update when the model emits dataChanged
            # because it's bound to the model's roles
            pass

    # ------------------------------------------------------------------
    # External drop configuration (for compatibility)
    # ------------------------------------------------------------------

    def configure_external_drop(
        self, *, handler=None, validator=None
    ) -> None:
        """Configure external drop handling.

        Note: Drag-and-drop from external sources is not yet implemented in
        the QML gallery view. This method is provided for API compatibility.
        External drops will be silently ignored until this feature is
        implemented. Use the legacy gallery view (IPHOTO_LEGACY_GALLERY=1)
        if external drop support is required.
        """
        # Future implementation could use QML DropArea to handle external drops
        if handler is not None:
            logger.debug("External drop handler configured but not yet implemented in QML view")

    def setAcceptDrops(self, accept: bool) -> None:
        """Enable/disable drop acceptance."""
        self._quick_widget.setAcceptDrops(accept)


class QmlGalleryPageWidget(QWidget):
    """Page widget containing the QML-based gallery grid view.

    Drop-in replacement for GalleryPageWidget that uses QML rendering.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("galleryPage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.grid_view = QmlGalleryGridView()
        self.grid_view.setObjectName("galleryGridView")
        layout.addWidget(self.grid_view)


__all__ = [
    "QmlGalleryGridView",
    "QmlGalleryPageWidget",
    "ThumbnailImageProvider",
]
