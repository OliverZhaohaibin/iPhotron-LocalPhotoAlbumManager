"""QML-based gallery grid widget wrapper."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import (
    QModelIndex,
    QObject,
    QUrl,
    Qt,
    Signal,
    Slot,
    QTimer,
    QItemSelectionModel
)
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtQuick import QQuickItem
from PySide6.QtWidgets import QWidget, QAbstractItemView
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent

from ..models.asset_list_model import AssetListModel
from ..models.asset_model import AssetModel
from .thumbnail_image_provider import ThumbnailImageProvider

logger = logging.getLogger(__name__)


class QmlGalleryWidget(QQuickWidget):
    """Wrapper for the QML GalleryView."""

    # Replicate AssetGrid signals
    itemClicked = Signal(QModelIndex)
    requestPreview = Signal(QModelIndex)
    previewReleased = Signal()
    previewCancelled = Signal()
    visibleRowsChanged = Signal(int, int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)

        self.setSizePolicy(
            QWidget.QSizePolicy.Policy.Expanding,
            QWidget.QSizePolicy.Policy.Expanding
        )

        self._model: Optional[QObject] = None
        self._selection_model: Optional[QItemSelectionModel] = None
        self._preview_enabled = True

        # Drag and Drop
        self._external_drop_enabled = False
        self._drop_handler: Optional[Callable[[List[Path]], None]] = None
        self._drop_validator: Optional[Callable[[List[Path]], bool]] = None

    def setModel(self, model: QObject) -> None:
        """Bind the asset model and initialize the QML context."""
        self._model = model
        self._selection_model = QItemSelectionModel(model)

        # Extract underlying AssetListModel for the Image Provider
        # The model passed here is typically AssetModel (QSortFilterProxyModel)
        source_model = model
        if isinstance(model, AssetModel):
             source_model = model.source_model()

        if isinstance(source_model, AssetListModel):
            provider = ThumbnailImageProvider(source_model._cache_manager)
            self.engine().addImageProvider("thumbnail", provider)
        else:
            logger.warning("QmlGalleryWidget: Could not resolve AssetListModel for ImageProvider")

        # Setup Context
        root_ctx = self.rootContext()
        root_ctx.setContextProperty("assetModel", model)
        root_ctx.setContextProperty("selectionModel", None) # Placeholder

        # Icon path
        icon_path = Path(__file__).parent.parent / "icon"
        root_ctx.setContextProperty("iconPath", str(icon_path.resolve()).replace("\\", "/"))

        # Load Source
        qml_path = Path(__file__).parent.parent / "qml" / "GalleryView.qml"
        self.setSource(QUrl.fromLocalFile(str(qml_path.resolve())))

        # Connect Signals from Root Object
        root_obj = self.rootObject()
        if root_obj:
            root_obj.itemClicked.connect(self._on_qml_item_clicked)
            root_obj.requestPreview.connect(self._on_qml_request_preview)
            root_obj.previewReleased.connect(self.previewReleased)
            root_obj.previewCancelled.connect(self.previewCancelled)
            root_obj.visibleRowsChanged.connect(self.visibleRowsChanged)

            # Sync selection mode state
            root_obj.setSelectionMode(False) # Default

    def selectionModel(self) -> QItemSelectionModel:
        """Return the selection model for controller compatibility."""
        if self._selection_model is None and self._model:
             self._selection_model = QItemSelectionModel(self._model)
        return self._selection_model

    def setItemDelegate(self, delegate: QObject) -> None:
        """Stub for compatibility with DataManager."""
        pass

    def setContextMenuPolicy(self, policy) -> None:
        super().setContextMenuPolicy(policy)

    # ------------------------------------------------------------------
    # Selection Mode
    # ------------------------------------------------------------------
    def set_selection_mode_enabled(self, enabled: bool) -> None:
        """Toggle selection mode in QML."""
        root = self.rootObject()
        if root:
            # Invoke method or set property
            # QML has `function setSelectionMode(enabled)`
            # We can use QMetaObject.invokeMethod or set property `selectionModeActive`
            root.setProperty("selectionModeActive", enabled)

    def selection_mode_active(self) -> bool:
        root = self.rootObject()
        if root:
            return root.property("selectionModeActive")
        return False

    def clearSelection(self) -> None:
        if self._selection_model:
            self._selection_model.clearSelection()

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------
    def set_preview_enabled(self, enabled: bool) -> None:
        self._preview_enabled = enabled

    def preview_enabled(self) -> bool:
        return self._preview_enabled

    # ------------------------------------------------------------------
    # External Drop (Ported from AssetGrid)
    # ------------------------------------------------------------------
    def configure_external_drop(
        self,
        *,
        handler: Optional[Callable[[List[Path]], None]] = None,
        validator: Optional[Callable[[List[Path]], bool]] = None,
    ) -> None:
        self._drop_handler = handler
        self._drop_validator = validator
        self._external_drop_enabled = handler is not None
        self.setAcceptDrops(self._external_drop_enabled)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not self._external_drop_enabled:
            super().dragEnterEvent(event)
            return
        paths = self._extract_local_files(event)
        if not paths:
            event.ignore()
            return
        if self._drop_validator is not None and not self._drop_validator(paths):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if not self._external_drop_enabled:
            super().dragMoveEvent(event)
            return
        paths = self._extract_local_files(event)
        if not paths:
            event.ignore()
            return
        if self._drop_validator is not None and not self._drop_validator(paths):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        if not self._external_drop_enabled or self._drop_handler is None:
            super().dropEvent(event)
            return
        paths = self._extract_local_files(event)
        if not paths:
            event.ignore()
            return
        if self._drop_validator is not None and not self._drop_validator(paths):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self._drop_handler(paths)

    def _extract_local_files(self, event: QDropEvent | QDragEnterEvent | QDragMoveEvent) -> List[Path]:
        mime = event.mimeData()
        if mime is None:
            return []
        urls = getattr(mime, "urls", None)
        if not callable(urls):
            return []
        seen: set[Path] = set()
        paths: List[Path] = []
        for url in urls():
            if not url.isLocalFile():
                continue
            local = Path(url.toLocalFile()).expanduser()
            if local in seen:
                continue
            seen.add(local)
            paths.append(local)
        return paths

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------
    @Slot(int)
    def _on_qml_item_clicked(self, row: int) -> None:
        if self._model:
            index = self._model.index(row, 0)
            if index.isValid():
                self.itemClicked.emit(index)
                if self._selection_model:
                    self._selection_model.setCurrentIndex(
                        index,
                        QItemSelectionModel.SelectionFlag.ClearAndSelect
                    )

    @Slot(int)
    def _on_qml_request_preview(self, row: int) -> None:
        if not self._preview_enabled:
            return
        if self._model:
            index = self._model.index(row, 0)
            if index.isValid():
                self.requestPreview.emit(index)

    def setFocus(self) -> None:
        super().setFocus()
        if self.rootObject():
            self.rootObject().forceActiveFocus()
