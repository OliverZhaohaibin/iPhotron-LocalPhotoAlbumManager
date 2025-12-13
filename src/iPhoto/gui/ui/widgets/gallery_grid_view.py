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
from PySide6.QtGui import QColor, QDragEnterEvent, QDragMoveEvent, QDropEvent, QSurfaceFormat
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QWidget

from ..models.roles import Roles
from ..qml.thumbnail_provider import ThumbnailProvider

logger = logging.getLogger(__name__)


class SelectionModelShim(QObject):
    """
    Mimics QItemSelectionModel API but operates on the AssetListModel's IS_SELECTED role.
    This allows existing controllers to interact with the selection without knowing
    about the underlying QML/Model architecture.
    """

    selectionChanged = Signal(QItemSelection, QItemSelection)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self._model = model

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
        Apply selection changes to the model.
        Note: This is a simplified implementation focusing on Select, Toggle, and Clear.
        """
        if isinstance(selection, QModelIndex):
            indexes = [selection]
        elif isinstance(selection, QItemSelection):
            indexes = selection.indexes()
        else:
            return

        is_clear = command & QItemSelectionModel.SelectionFlag.Clear
        is_select = command & QItemSelectionModel.SelectionFlag.Select
        is_deselect = command & QItemSelectionModel.SelectionFlag.Deselect
        is_toggle = command & QItemSelectionModel.SelectionFlag.Toggle

        # If ClearAndSelect, first clear everything (inefficient implementation, but safe)
        if command & QItemSelectionModel.SelectionFlag.ClearAndSelect:
            self.clear()
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

    def clear(self) -> None:
        """Clear all selected items."""
        # Optimization: Iterate rows and set IS_SELECTED to False where True
        # Since we don't expose iterator, we iterate indices.
        # Ideally AssetListModel would expose a 'clear_selection' method.
        # But we use setData loop for now.
        row_count = self._model.rowCount()
        for i in range(row_count):
            index = self._model.index(i, 0)
            if self._model.data(index, Roles.IS_SELECTED):
                self._model.setData(index, False, Roles.IS_SELECTED)

    def selectedIndexes(self) -> List[QModelIndex]:
        """Return a list of all selected indexes."""
        selected = []
        row_count = self._model.rowCount()
        for i in range(row_count):
            index = self._model.index(i, 0)
            if self._model.data(index, Roles.IS_SELECTED):
                selected.append(index)
        return selected

    def currentIndex(self) -> QModelIndex:
        # We don't strictly track 'current' separate from selection or focus in this Shim
        # But controllers might ask for it. Return first selected or invalid.
        # AssetListModel has IS_CURRENT role. We should use that.
        row_count = self._model.rowCount()
        for i in range(row_count):
            index = self._model.index(i, 0)
            if self._model.data(index, Roles.IS_CURRENT):
                return index
        return QModelIndex()


class GalleryQuickWidget(QQuickWidget):
    """
    QML-hosting widget that replaces the legacy QListView-based GalleryGridView.
    It exposes an API compatible with AssetGrid to minimize controller refactoring.
    """

    # Signals compatible with AssetGrid
    clicked = Signal(QModelIndex)
    doubleClicked = Signal(QModelIndex)
    visibleRowsChanged = Signal(int, int)
    customContextMenuRequested = Signal(QPoint)
    requestPreview = Signal(QModelIndex)
    previewReleased = Signal()
    previewCancelled = Signal()

    # Drag & Drop signals handled internally via DropArea -> filesDropped shim

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Disable the alpha buffer to prevent transparency issues with the DWM
        # when using a frameless window configuration.
        gl_format = QSurfaceFormat()
        gl_format.setAlphaBufferSize(0)
        self.setFormat(gl_format)

        self.setClearColor(QColor("#2b2b2b"))
        self.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        # Removed WA_OpaquePaintEvent to allow parent widget to paint background if GL context fails or is transparent
        # self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

        self.statusChanged.connect(self._on_status_changed)
        self.sceneGraphError.connect(self._on_scene_graph_error)

        # Internal state
        self._model = None
        self._selection_shim: Optional[SelectionModelShim] = None
        self._drop_handler: Optional[Callable[[List[Path]], None]] = None
        self._last_context_menu_index: Optional[QModelIndex] = None

        # Load QML
        # We delay source loading until setModel to have the model ready?
        # Or load immediately and inject model later.
        # Standard QQuickWidget usage is load source, but we need image provider.

        # We defer QML loading until setModel to ensure ThumbnailProvider has the model.
        # But setModel might be called later.
        # Actually ThumbnailProvider needs model.
        # So we wait for setModel.

    def setModel(self, model) -> None:
        """Set the model and initialize the QML engine."""
        self._model = model
        self._selection_shim = SelectionModelShim(model, self)

        # Register Image Provider
        # We use the source model (AssetListModel) for the provider
        # If 'model' is a proxy (AssetModel), get source.
        source_model = model
        if hasattr(model, "sourceModel"):
            source_model = model.sourceModel()

        provider = ThumbnailProvider(source_model)
        self.engine().addImageProvider("thumbnails", provider)

        # Expose model to QML
        self.rootContext().setContextProperty("assetModel", self._model)

        # Load QML
        qml_path = Path(__file__).parent.parent / "qml" / "GalleryGrid.qml"
        self.setSource(QUrl.fromLocalFile(str(qml_path.resolve())))

        # Connect signals from QML root object
        root = self.rootObject()
        if root:
            root.itemClicked.connect(self._on_item_clicked)
            root.visibleRowsChanged.connect(self._on_visible_rows_changed)
            root.showContextMenu.connect(self._on_show_context_menu)
            root.filesDropped.connect(self._on_files_dropped)

    def model(self):
        return self._model

    def selectionModel(self) -> SelectionModelShim:
        """Return the selection model shim."""
        return self._selection_shim

    def viewport(self) -> QWidget:
        """
        Shim for QAbstractItemView.viewport().
        Controllers use this to mapToGlobal.
        QQuickWidget is the viewport itself in this context.
        """
        return self

    def indexAt(self, point: QPoint) -> QModelIndex:
        """
        Shim for indexAt.
        Since mapping generic QPoint to QML item index is hard without round-trip,
        we rely on the fact that controllers usually call this during a context menu event.
        We cache the index from the 'showContextMenu' signal.
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

        self._model.setData(index, True, Roles.IS_CURRENT)

    # ------------------------------------------------------------------
    # QML Signal Handlers
    # ------------------------------------------------------------------
    @Slot(str)
    def _on_status_changed(self, status):
        if status == QQuickWidget.Status.Error:
            for error in self.errors():
                logger.error(f"QML Error: {error.toString()}")

    @Slot(str, str)
    def _on_scene_graph_error(self, error, message):
        logger.error(f"SceneGraph Error: {error} - {message}")

    @Slot(int, int)
    def _on_item_clicked(self, row: int, modifiers: int) -> None:
        index = self._model.index(row, 0)
        if index.isValid():
            # Update current index in model
            self.setCurrentIndex(index)
            self.clicked.emit(index)

    @Slot(int)
    def _on_item_double_clicked(self, row: int) -> None:
        index = self._model.index(row, 0)
        if index.isValid():
            self.setCurrentIndex(index)
            self.doubleClicked.emit(index)

    @Slot(int, int)
    def _on_visible_rows_changed(self, first: int, last: int) -> None:
        self.visibleRowsChanged.emit(first, last)

    @Slot(int, int, int)
    def _on_show_context_menu(self, row: int, global_x: int, global_y: int) -> None:
        index = self._model.index(row, 0)
        self._last_context_menu_index = index
        # We need to map global back to local for the signal,
        # because ContextMenuController uses indexAt(point) with the point from signal.
        local_pt = self.mapFromGlobal(QPoint(global_x, global_y))
        self.customContextMenuRequested.emit(local_pt)

    @Slot(list)
    def _on_files_dropped(self, urls: List[QUrl]) -> None:
        if self._drop_handler:
            paths = []
            for url in urls:
                if isinstance(url, str): # QML might pass strings
                    url = QUrl(url)
                if url.isLocalFile():
                    paths.append(Path(url.toLocalFile()))
            if paths:
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
        # Validator not easily supported in QML DropArea without specialized C++ class
        # But we can just accept all and filter in handler if needed.
