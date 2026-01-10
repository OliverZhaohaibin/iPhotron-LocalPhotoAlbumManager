"""QML-driven grid view for the gallery layout."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, List, Optional, TYPE_CHECKING

from PySide6.QtCore import QModelIndex, QPoint, QSize, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPalette, QPixmap, QSurfaceFormat
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider, QQuickWindow
from PySide6.QtQuickWidgets import QQuickWidget

if TYPE_CHECKING:
    from ..theme_manager import ThemeColors


class ThumbnailImageProvider(QQuickImageProvider):
    """QML image provider that serves thumbnails from the asset model's cache."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Pixmap)
        self._model = None
        self._cache_manager = None

    def set_model(self, model) -> None:
        """Set the asset model to use for thumbnail lookups."""
        self._model = model
        if model is not None:
            source_model = model.sourceModel() if hasattr(model, 'sourceModel') else model
            if hasattr(source_model, '_cache_manager'):
                self._cache_manager = source_model._cache_manager

    def requestPixmap(self, id: str, size: QSize, requestedSize: QSize) -> QPixmap:
        """Return the thumbnail pixmap for the given relative path."""
        try:
            # Parse the id - it includes the rel path and optionally a version query parameter
            rel = id.split('?')[0] if '?' in id else id

            if not self._model or not self._cache_manager:
                return QPixmap()

            # Try to get from cache first
            pixmap = self._cache_manager.thumbnail_for(rel)
            if pixmap is not None:
                return pixmap

            # If not in cache, return a placeholder (thumbnail will be loaded async)
            # _placeholder_for is a private method that takes (rel, is_video) args
            # We default to is_video=False since we don't know the media type from just rel path
            placeholder = self._cache_manager._placeholder_for(rel, False)
            if placeholder is not None:
                return placeholder
        except Exception:
            # Fallback for any error during lookup
            logging.exception("Error loading thumbnail for id: %s", id)

        # Return empty pixmap as fallback
        return QPixmap()


class GalleryQuickWidget(QQuickWidget):
    """QML-based grid view that provides an opaque background for frameless windows.
    
    This widget wraps a QML GridView component and ensures proper background
    rendering to avoid transparency issues with DWM when using frameless windows.
    """

    # Signals that mirror the AssetGrid interface for compatibility
    itemClicked = Signal(QModelIndex)
    requestPreview = Signal(QModelIndex)
    previewReleased = Signal()
    previewCancelled = Signal()
    visibleRowsChanged = Signal(int, int)

    def __init__(self, parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)

        # Enable 8-bit alpha buffer for proper blending and to avoid transparency issues with DWM
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        self.setFormat(fmt)

        # Configure the widget for opaque rendering
        # WA_AlwaysStackOnTop is required to ensure the QQuickWidget renders correctly
        # when embedded in a window with WA_TranslucentBackground enabled.
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(True)

        # Force a clear color immediately to prevent transparency during initialization
        self.setClearColor(QColor("#2b2b2b"))

        # Set resize mode to follow widget size
        self.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)

        # Store model reference and theme colors
        self._model = None
        self._theme_colors: Optional["ThemeColors"] = None
        self._selection_mode_enabled = False
        self._preview_enabled = True
        self._external_drop_enabled = False
        self._drop_handler: Optional[Callable[[List[Path]], None]] = None
        self._drop_validator: Optional[Callable[[List[Path]], bool]] = None

        # Create and register thumbnail image provider
        self._thumbnail_provider = ThumbnailImageProvider()
        self.engine().addImageProvider("thumbnails", self._thumbnail_provider)

        # Load QML file
        qml_dir = Path(__file__).parent.parent / "qml"
        qml_path = qml_dir / "GalleryGrid.qml"

        # Set up the QML context
        self.engine().addImportPath(str(qml_dir))

        # Load the QML component
        self.setSource(QUrl.fromLocalFile(str(qml_path)))

        # Connect to the status changed signal for error handling
        self.statusChanged.connect(self._on_status_changed)
        quick_window = self.quickWindow()
        if quick_window is not None:
            quick_window.sceneGraphError.connect(self._on_scene_graph_error)

        # Connect QML signals to Python after component loads
        self._connect_qml_signals()

    def _on_status_changed(self, status: QQuickWidget.Status) -> None:
        """Handle status changes during QML loading."""
        if status == QQuickWidget.Status.Error:
            for error in self.errors():
                print(f"QML Error: {error.toString()}")

    def _on_scene_graph_error(self, error: QQuickWindow.SceneGraphError, message: str) -> None:
        """Handle OpenGL errors from the scene graph."""
        print(f"SceneGraph Error ({error}): {message}")

    def _connect_qml_signals(self) -> None:
        """Connect QML signals to Python slots."""
        root = self.rootObject()
        if root is None:
            return

        root.itemClicked.connect(self._on_item_clicked)
        root.itemDoubleClicked.connect(self._on_item_double_clicked)
        root.currentIndexChanged.connect(self._on_current_index_changed)
        root.showContextMenu.connect(self._on_show_context_menu)
        root.visibleRowsChanged.connect(self._on_visible_rows_changed)
        root.filesDropped.connect(self._on_files_dropped)

    @Slot(int, int)
    def _on_item_clicked(self, index: int, modifiers: int) -> None:
        """Handle item click from QML."""
        if self._model is None:
            return
        model_index = self._model.index(index, 0)
        if model_index.isValid():
            self.itemClicked.emit(model_index)

    @Slot(int)
    def _on_item_double_clicked(self, index: int) -> None:
        """Handle item double-click from QML."""
        if self._model is None:
            return
        model_index = self._model.index(index, 0)
        if model_index.isValid():
            self.itemClicked.emit(model_index)

    @Slot(int)
    def _on_current_index_changed(self, index: int) -> None:
        """Handle current index change from QML."""
        pass  # Used internally by QML for visual feedback

    @Slot(int, int, int)
    def _on_show_context_menu(self, index: int, globalX: int, globalY: int) -> None:
        """Handle context menu request from QML."""
        # Emit customContextMenuRequested signal for compatibility
        viewport_pos = self.mapFromGlobal(QPoint(globalX, globalY))
        self.customContextMenuRequested.emit(viewport_pos)

    @Slot(int, int)
    def _on_visible_rows_changed(self, first: int, last: int) -> None:
        """Handle visible rows change from QML."""
        self.visibleRowsChanged.emit(first, last)

    @Slot(list)
    def _on_files_dropped(self, urls: list) -> None:
        """Handle files dropped from QML."""
        if not self._external_drop_enabled or self._drop_handler is None:
            return

        paths = []
        for url in urls:
            if isinstance(url, QUrl):
                if url.isLocalFile():
                    paths.append(Path(url.toLocalFile()))
            elif isinstance(url, str):
                if url.startswith("file://"):
                    paths.append(Path(QUrl(url).toLocalFile()))
                else:
                    paths.append(Path(url))

        if not paths:
            return

        if self._drop_validator is not None and not self._drop_validator(paths):
            return

        self._drop_handler(paths)

    def setModel(self, model) -> None:  # type: ignore[override]
        """Set the data model for the grid view."""
        self._model = model

        # Update the thumbnail provider with the model
        if self._thumbnail_provider is not None:
            self._thumbnail_provider.set_model(model)

        # Expose model to QML context
        root_context = self.rootContext()
        if root_context:
            root_context.setContextProperty("assetModel", model)

        # Connect model signals
        if model is not None:
            model.modelReset.connect(self._on_model_reset)
            model.rowsInserted.connect(self._on_rows_inserted)
            model.rowsRemoved.connect(self._on_rows_removed)

    def _on_model_reset(self) -> None:
        """Handle model reset."""
        pass  # QML binding handles this automatically

    def _on_rows_inserted(self) -> None:
        """Handle rows inserted."""
        pass  # QML binding handles this automatically

    def _on_rows_removed(self) -> None:
        """Handle rows removed."""
        pass  # QML binding handles this automatically

    def model(self):
        """Return the current model."""
        return self._model

    def selectionModel(self):
        """Return selection model - for compatibility with widget-based code.
        
        Note: QML handles selection internally, so we return None here.
        Selection is managed through the model's IS_SELECTED role.
        """
        return None

    def clearSelection(self) -> None:
        """Clear all selection in the grid."""
        if self._model is None:
            return

        # Import Roles here to avoid circular imports
        from ..models.roles import Roles

        # Get the source model if this is a proxy model
        source_model = self._model
        if hasattr(self._model, 'sourceModel'):
            source_model = self._model.sourceModel()

        # Clear is_selected flag on all rows with a single dataChanged signal
        row_count = source_model.rowCount()
        if row_count == 0:
            return

        # Access internal rows directly for efficient batch clear
        if hasattr(source_model, '_state_manager') and hasattr(source_model._state_manager, 'rows'):
            rows = source_model._state_manager.rows
            for row in rows:
                row["is_selected"] = False

            # Emit single dataChanged for all rows
            first_index = source_model.index(0, 0)
            last_index = source_model.index(row_count - 1, 0)
            source_model.dataChanged.emit(first_index, last_index, [Roles.IS_SELECTED])

    def indexAt(self, point: QPoint) -> QModelIndex:
        """Get model index at the given point - stub for compatibility.

        Note: This method returns an invalid QModelIndex because QML handles
        hit testing internally. For context menus, the index is determined
        from QML signals.
        """
        return QModelIndex()

    def viewport(self):
        """Return self as viewport for compatibility."""
        return self

    def apply_theme(self, colors: "ThemeColors") -> None:
        """Apply theme colors to the QML view."""
        self._theme_colors = colors

        # Apply background color to the widget
        self._apply_background_color(colors.window_background)

        # Set clear color for the QML view
        self.setClearColor(colors.window_background)

        # Sync colors to QML
        self._sync_theme_to_qml()

    def _apply_background_color(self, color: QColor) -> None:
        """Apply background color to the widget palette."""
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, color)
        palette.setColor(QPalette.ColorRole.Base, color)
        self.setPalette(palette)
        self.setAutoFillBackground(True)

    def _sync_theme_to_qml(self) -> None:
        """Sync theme colors to QML root object."""
        if self._theme_colors is None:
            return

        root = self.rootObject()
        if root is None:
            return

        colors = self._theme_colors

        # Set QML properties
        root.setProperty("backgroundColor", colors.window_background)

        # Calculate item background color based on theme
        if colors.is_dark:
            item_bg = QColor(colors.window_background).darker(115)
        else:
            item_bg = QColor(colors.window_background).darker(105)
        root.setProperty("itemBackgroundColor", item_bg)

        root.setProperty("selectionBorderColor", colors.accent_color)
        root.setProperty("currentBorderColor", colors.text_primary)

    # ------------------------------------------------------------------
    # Selection mode toggling (compatibility with AssetGrid interface)
    # ------------------------------------------------------------------
    def selection_mode_active(self) -> bool:
        """Return ``True`` when multi-selection mode is currently enabled."""
        return self._selection_mode_enabled

    def set_selection_mode_enabled(self, enabled: bool) -> None:
        """Switch between the default single selection and multi-selection mode."""
        desired_state = bool(enabled)
        if self._selection_mode_enabled == desired_state:
            return
        self._selection_mode_enabled = desired_state

        # Sync to QML
        root = self.rootObject()
        if root is not None:
            root.setProperty("selectionMode", desired_state)

        if not desired_state:
            self.clearSelection()

        self.set_preview_enabled(not desired_state)

    # ------------------------------------------------------------------
    # Preview configuration (compatibility with AssetGrid interface)
    # ------------------------------------------------------------------
    def set_preview_enabled(self, enabled: bool) -> None:
        """Enable or disable the long-press preview workflow."""
        self._preview_enabled = bool(enabled)

    def preview_enabled(self) -> bool:
        """Return ``True`` when long-press previews are currently allowed."""
        return self._preview_enabled

    # ------------------------------------------------------------------
    # External file drop configuration (compatibility with AssetGrid interface)
    # ------------------------------------------------------------------
    def configure_external_drop(
        self,
        *,
        handler: Optional[Callable[[List[Path]], None]] = None,
        validator: Optional[Callable[[List[Path]], bool]] = None,
    ) -> None:
        """Enable or disable external drop support for the grid view."""
        self._drop_handler = handler
        self._drop_validator = validator
        self._external_drop_enabled = handler is not None

    def setContextMenuPolicy(self, policy: Qt.ContextMenuPolicy) -> None:
        """Set context menu policy - for compatibility."""
        super().setContextMenuPolicy(policy)

    def setItemDelegate(self, delegate) -> None:
        """Set item delegate - stub for compatibility.
        
        QML handles rendering internally, so this is a no-op.
        """
        pass

    def itemDelegate(self):
        """Return item delegate - stub for compatibility."""
        return None


# Keep GalleryGridView as an alias for backwards compatibility
GalleryGridView = GalleryQuickWidget
