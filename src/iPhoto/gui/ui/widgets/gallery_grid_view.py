"""QML-based gallery grid view replacement."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional, Union

from PySide6.QtCore import (
    QModelIndex,
    QObject,
    QPoint,
    Qt,
    QUrl,
    QRect,
    Signal,
    Slot,
    QItemSelection,
    QItemSelectionModel,
    QEvent,
)
from PySide6.QtGui import (
    QColor,
    QPalette,
    QSurfaceFormat,
)
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout

from ..models.roles import Roles
from ..qml.thumbnail_provider import ThumbnailProvider
from ..theme_manager import ThemeColors

logger = logging.getLogger(__name__)


class SelectionModelShim(QObject):
    """
    Mimics QItemSelectionModel API but operates on the AssetListModel's IS_SELECTED role.
    This allows existing controllers to interact with the selection without knowing
    about the underlying QML/Model architecture.
    """

    selectionChanged = Signal(QItemSelection, QItemSelection)
    currentChanged = Signal(QModelIndex, QModelIndex)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self._model = model
        self._selection_cache: Optional[List[QModelIndex]] = None
        self._current_index_cache: Optional[QModelIndex] = None

        # Connect signals to invalidate cache
        self._model.dataChanged.connect(self._on_data_changed)
        self._model.rowsInserted.connect(self._invalidate_cache)
        self._model.rowsRemoved.connect(self._invalidate_cache)
        self._model.modelReset.connect(self._invalidate_cache)
        self._model.layoutChanged.connect(self._invalidate_cache)

    @Slot()
    def _invalidate_cache(self) -> None:
        self._selection_cache = None
        self._current_index_cache = None

    @Slot(QModelIndex, QModelIndex, list)
    def _on_data_changed(self, top: QModelIndex, bottom: QModelIndex, roles: Optional[List[int]] = None) -> None:
        if not roles or Roles.IS_SELECTED in roles:
            self._selection_cache = None
        if not roles or Roles.IS_CURRENT in roles:
            self._current_index_cache = None

    def isSelected(self, index: QModelIndex) -> bool:
        if not index.isValid():
            return False
        return self._model.data(index, Roles.IS_SELECTED)

    def select(
        self,
        selection: Union[QModelIndex, QItemSelection],
        command: QItemSelectionModel.SelectionFlag,
    ) -> None:
        """
        Apply selection changes to the model and emit signals.
        """
        if isinstance(selection, QModelIndex):
            indexes = [selection]
        elif isinstance(selection, QItemSelection):
            indexes = selection.indexes()
        else:
            return

        is_select = command & QItemSelectionModel.SelectionFlag.Select
        is_deselect = command & QItemSelectionModel.SelectionFlag.Deselect
        is_toggle = command & QItemSelectionModel.SelectionFlag.Toggle

        # Track changes for signal emission
        selected_indexes = []
        deselected_indexes = []

        # If ClearAndSelect, first clear everything
        if command & QItemSelectionModel.SelectionFlag.ClearAndSelect:
            # We need to find what will be deselected
            # Use row numbers for robust comparison
            rows_to_keep = {ix.row() for ix in indexes if ix.isValid()}
            row_count = self._model.rowCount()
            for i in range(row_count):
                idx = self._model.index(i, 0)
                if self._model.data(idx, Roles.IS_SELECTED):
                    if idx.row() not in rows_to_keep:  # Unless we are re-selecting it later
                        self._model.setData(idx, False, Roles.IS_SELECTED)
                        deselected_indexes.append(idx)
            is_select = True

        for index in indexes:
            if not index.isValid():
                continue

            current = self._model.data(index, Roles.IS_SELECTED)
            new_state = current

            if is_toggle:
                new_state = not current
            elif is_select:
                new_state = True
            elif is_deselect:
                new_state = False

            if current != new_state:
                self._model.setData(index, new_state, Roles.IS_SELECTED)
                if new_state:
                    selected_indexes.append(index)
                else:
                    deselected_indexes.append(index)

        # Emit signal if changes occurred
        if selected_indexes or deselected_indexes:
            # Convert lists to QItemSelection
            # Since we have scattered indexes, we just add them one by one or create ranges?
            # QItemSelection from list of indexes:
            sel_new = QItemSelection()
            for idx in selected_indexes:
                sel_new.select(idx, idx)

            sel_old = QItemSelection()
            for idx in deselected_indexes:
                sel_old.select(idx, idx)

            self.selectionChanged.emit(sel_new, sel_old)

    def clear(self) -> None:
        """Clear all selected items."""
        deselected_indexes = []
        row_count = self._model.rowCount()
        for i in range(row_count):
            index = self._model.index(i, 0)
            if self._model.data(index, Roles.IS_SELECTED):
                self._model.setData(index, False, Roles.IS_SELECTED)
                deselected_indexes.append(index)

        if deselected_indexes:
            sel_old = QItemSelection()
            for idx in deselected_indexes:
                sel_old.select(idx, idx)
            self.selectionChanged.emit(QItemSelection(), sel_old)

    def selectedIndexes(self) -> List[QModelIndex]:
        """Return a list of all selected indexes."""
        if self._selection_cache is not None:
            return list(self._selection_cache)

        selected = []
        row_count = self._model.rowCount()
        for i in range(row_count):
            index = self._model.index(i, 0)
            if self._model.data(index, Roles.IS_SELECTED):
                selected.append(index)

        self._selection_cache = selected
        return list(selected)

    def currentIndex(self) -> QModelIndex:
        if self._current_index_cache is not None:
            if self._current_index_cache.isValid():
                return self._current_index_cache
            # If invalid, it might mean 'no current item', so we still trust it
            # unless we invalidated it to None.
            return self._current_index_cache

        row_count = self._model.rowCount()
        for i in range(row_count):
            index = self._model.index(i, 0)
            if self._model.data(index, Roles.IS_CURRENT):
                self._current_index_cache = index
                return index

        self._current_index_cache = QModelIndex()
        return self._current_index_cache

    def setCurrentIndex(self, index: QModelIndex, command: QItemSelectionModel.SelectionFlag) -> None:
        """Set current index and optionally select."""
        old_current = self.currentIndex()
        if index == old_current:
            return

        if index.isValid() and index.model() != self._model:
            logger.warning("SelectionModelShim: Attempted to set current index from a different model.")
            return

        # Unset old current
        if old_current.isValid():
            self._model.setData(old_current, False, Roles.IS_CURRENT)

        # Set new current
        if index.isValid():
            if not self._model.setData(index, True, Roles.IS_CURRENT):
                logger.warning(f"SelectionModelShim: Failed to set current index for row {index.row()}")
        else:
            logger.debug("SelectionModelShim: Clearing current index (invalid index passed).")

        self.currentChanged.emit(index, old_current)

        if command != QItemSelectionModel.SelectionFlag.NoUpdate:
            self.select(index, command)


class GalleryQuickWidget(QQuickWidget):
    """
    QML-hosting widget that replaces the legacy QListView-based GalleryGridView.
    It exposes an API compatible with AssetGrid to minimize controller refactoring.
    """

    # Signals compatible with AssetGrid
    clicked = Signal(QModelIndex)
    itemClicked = clicked
    doubleClicked = Signal(QModelIndex)
    visibleRowsChanged = Signal(int, int)
    customContextMenuRequested = Signal(QPoint)
    requestPreview = Signal(QModelIndex)
    previewReleased = Signal()
    previewCancelled = Signal()

    # Drag & Drop signals handled internally via DropArea -> filesDropped shim

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme_colors: ThemeColors | None = None

        # Disable the alpha buffer to prevent transparency issues with the DWM
        # when using a frameless window configuration.
        gl_format = QSurfaceFormat()
        gl_format.setAlphaBufferSize(0)
        self.setFormat(gl_format)

        # Force opaque background
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        default_bg = self.palette().color(QPalette.ColorRole.Base)
        if not default_bg.isValid():
            default_bg = QColor("#2b2b2b")
        self._apply_background_color(default_bg)
        self.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)

        self.statusChanged.connect(self._on_status_changed)
        self.sceneGraphError.connect(self._on_scene_graph_error)

        # Internal state
        self._model = None
        self._selection_shim: Optional[SelectionModelShim] = None
        self._drop_handler: Optional[Callable[[List[Path]], None]] = None
        self._drop_validator: Optional[Callable[[List[Path]], bool]] = None
        self._last_context_menu_index: Optional[QModelIndex] = None

    def setModel(self, model) -> None:
        """Set the model and initialize the QML engine."""
        self._model = model
        self._selection_shim = SelectionModelShim(model, self)

        # Register Image Provider
        source_model = model
        if hasattr(model, "sourceModel"):
            source_model = model.sourceModel()

        provider = ThumbnailProvider(source_model)
        self.engine().addImageProvider("thumbnails", provider)

        # Expose model to QML
        self.rootContext().setContextProperty("assetModel", self._model)

        # Load QML
        qml_dir = Path(__file__).resolve().parent.parent / "qml"
        qml_path = qml_dir / "GalleryGrid.qml"

        if not qml_path.exists():
            logger.error(f"GalleryQuickWidget: QML file not found at {qml_path}")
            # Show visible error state
            layout = QVBoxLayout(self)
            label = QLabel(f"Critical Error: Gallery QML not found at\n{qml_path}", self)
            label.setStyleSheet("color: #ff5555; background: #222; font-size: 14px; padding: 20px;")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
            return

        logger.info(f"GalleryQuickWidget: Loading QML from {qml_path}")
        self.setSource(QUrl.fromLocalFile(str(qml_path)))

        # Connect signals from QML root object
        if self.status() == QQuickWidget.Status.Error:
            logger.error("GalleryQuickWidget: QML Engine reported errors.")
            for error in self.errors():
                logger.error(f"QML Error: {error.toString()}")
            return

        root = self.rootObject()
        if root is None:
            logger.error("GalleryQuickWidget: Failed to load root object from QML")
            return

        self._sync_theme_to_qml()
        root.itemClicked.connect(self._on_item_clicked)
        root.itemDoubleClicked.connect(self._on_item_double_clicked)
        root.currentIndexChanged.connect(self._on_current_index_changed)
        root.visibleRowsChanged.connect(self._on_visible_rows_changed)
        root.showContextMenu.connect(self._on_show_context_menu)
        root.filesDropped.connect(self._on_files_dropped)

    def model(self):
        return self._model

    def selectionModel(self) -> SelectionModelShim:
        """Return the selection model shim."""
        return self._selection_shim

    def viewport(self) -> QWidget:
        """Shim for QAbstractItemView.viewport()."""
        return self

    def indexAt(self, point: QPoint) -> QModelIndex:
        """
        Shim for indexAt.

        LIMITATION: This implementation does not perform actual geometric hit-testing
        against the QML scene graph. It relies on the `_last_context_menu_index`
        captured during the `customContextMenuRequested` event. Calls to this method
        outside of a context menu event handler will likely return an invalid index
        or a stale value.
        """
        if self._last_context_menu_index and self._last_context_menu_index.isValid():
            return self._last_context_menu_index
        return QModelIndex()

    def currentIndex(self) -> QModelIndex:
        """Return the current index."""
        if self._selection_shim:
            return self._selection_shim.currentIndex()
        return QModelIndex()

    def setCurrentIndex(self, index: QModelIndex) -> None:
        """Set the current index."""
        if not index.isValid() or not self._model:
            return

        # Use selection shim to ensure signals fire if needed, or direct update
        if self._selection_shim:
            self._selection_shim.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)

        # TODO: Implement Python -> QML GridView `currentIndex` synchronization if/when needed.
        # Currently, only QML -> Python synchronization is supported via signals. Implementing
        # the reverse direction would require exposing a root-level QML API to set the
        # GridView's `currentIndex` property from Python.

    def apply_theme(self, colors: ThemeColors) -> None:
        """Apply theme colors to the gallery view and QML surface."""
        self._theme_colors = colors
        self._apply_background_color(colors.window_background)
        self._sync_theme_to_qml()

    def _apply_background_color(self, color: QColor) -> None:
        """Ensure an opaque background is painted even before QML renders."""
        self.setClearColor(color)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, color)
        palette.setColor(QPalette.ColorRole.Base, color)
        self.setPalette(palette)
        self.setAutoFillBackground(True)

    def _sync_theme_to_qml(self) -> None:
        """Push stored theme colors into the QML root if available."""
        if self._theme_colors is None:
            return

        item_bg = QColor(self._theme_colors.window_background)
        # Keep subtle contrast between grid background and tile backgrounds.
        if self._theme_colors.is_dark:
            item_bg = item_bg.darker(115)
        else:
            item_bg = item_bg.darker(105)

        root = self.rootObject()
        if root:
            root.setProperty("backgroundColor", self._theme_colors.window_background)
            root.setProperty("itemBackgroundColor", item_bg)
            root.setProperty("selectionBorderColor", self._theme_colors.accent_color)
            root.setProperty("currentBorderColor", self._theme_colors.text_primary)

    # ------------------------------------------------------------------
    # QML Signal Handlers
    # ------------------------------------------------------------------
    @Slot(int)
    def _on_status_changed(self, status):
        if status == QQuickWidget.Status.Error:
            for error in self.errors():
                logger.error(f"QML Error: {error.toString()}")
        elif status == QQuickWidget.Status.Ready:
            logger.info("GalleryQuickWidget: QML Ready")

    @Slot(str, str)
    def _on_scene_graph_error(self, error, message):
        logger.error(f"SceneGraph Error: {error} - {message}")

    @Slot(int, int)
    def _on_item_clicked(self, row: int, modifiers: int) -> None:
        index = self._model.index(row, 0)
        if index.isValid():
            self.clicked.emit(index)
            # Selection logic handled by QML or Controller?
            # Original: Controller handles it via signals.
            # QML 'itemClicked' is emitted when not in Selection Mode inside QML.

    @Slot(int)
    def _on_current_index_changed(self, row: int) -> None:
        """Handle keyboard navigation or click updates from QML."""
        index = self._model.index(row, 0)
        if index.isValid():
            if self._selection_shim:
                self._selection_shim.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)

    @Slot(int)
    def _on_item_double_clicked(self, row: int) -> None:
        index = self._model.index(row, 0)
        if index.isValid():
            self.doubleClicked.emit(index)

    @Slot(int, int)
    def _on_visible_rows_changed(self, first: int, last: int) -> None:
        self.visibleRowsChanged.emit(first, last)

    @Slot(int, int, int)
    def _on_show_context_menu(self, row: int, global_x: int, global_y: int) -> None:
        index = self._model.index(row, 0)
        self._last_context_menu_index = index
        local_pt = self.mapFromGlobal(QPoint(global_x, global_y))
        self.customContextMenuRequested.emit(local_pt)

    @Slot(list)
    def _on_files_dropped(self, urls: List[QUrl]) -> None:
        if self._drop_handler:
            paths = []
            for url in urls:
                if isinstance(url, str):
                    url = QUrl(url)
                if url.isLocalFile():
                    paths.append(Path(url.toLocalFile()))

            if paths:
                if self._drop_validator and not self._drop_validator(paths):
                    return
                self._drop_handler(paths)

    # ------------------------------------------------------------------
    # AssetGrid Compatibility API
    # ------------------------------------------------------------------
    def set_selection_mode_enabled(self, enabled: bool) -> None:
        root = self.rootObject()
        if root:
            root.setProperty("selectionMode", enabled)

    def clearSelection(self) -> None:
        if self._selection_shim:
            self._selection_shim.clear()

    def set_preview_enabled(self, enabled: bool) -> None:
        pass

    def visualRect(self, index: QModelIndex) -> QRect:
        return QRect()

    def setItemDelegate(self, delegate) -> None:
        pass

    def configure_external_drop(
        self,
        *,
        handler: Optional[Callable[[List[Path]], None]] = None,
        validator: Optional[Callable[[List[Path]], bool]] = None,
    ) -> None:
        self._drop_handler = handler
        self._drop_validator = validator
