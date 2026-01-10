"""QML-driven gallery grid view."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, TYPE_CHECKING

from PySide6.QtCore import (
    QModelIndex,
    QItemSelection,
    QItemSelectionModel,
    QPoint,
    QSize,
    Qt,
    Signal,
    QUrl,
)
from PySide6.QtGui import QImage, QPalette, QPixmap, QColor, QSurfaceFormat, QPainter
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtQuickWidgets import QQuickWidget

from ..models.roles import Roles
from ..theme_manager import ThemeColors

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from ..models.asset_list.model import AssetListModel
    from ..models.asset_model import AssetModel


class _ThumbnailImageProvider(QQuickImageProvider):
    """Expose cached thumbnails to QML via the ``image://thumbnails`` scheme."""

    def __init__(self, model: "AssetListModel") -> None:
        super().__init__(QQuickImageProvider.Image)
        self._model = model

    def requestImage(  # type: ignore[override]  # pragma: no cover - exercised via QML
        self, identifier: str, size: QSize, requestedSize: QSize
    ) -> QImage:
        rel = identifier.split("?")[0].lstrip("/")
        pixmap = self._model.thumbnail_for_rel(rel)
        if not isinstance(pixmap, QPixmap):
            image = QImage()
            if size is not None:
                size.setWidth(0)
                size.setHeight(0)
            return image
        image = pixmap.toImage()
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image


class GalleryQuickWidget(QQuickWidget):
    """Gallery view backed by a QML grid."""

    itemClicked = Signal(QModelIndex)
    itemDoubleClicked = Signal(QModelIndex)
    requestPreview = Signal(QModelIndex)
    previewReleased = Signal()
    previewCancelled = Signal()
    visibleRowsChanged = Signal(int, int)
    contextMenuRequested = Signal(QModelIndex, QPoint)

    def __init__(self, parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._model: Optional["AssetModel"] = None
        self._selection_model: Optional[QItemSelectionModel] = None
        self._selection_mode_enabled = False
        self._external_drop_handler = None
        self._external_drop_validator = None
        self._theme_colors: Optional[ThemeColors] = None
        self._qml_loaded = False
        self._qml_signals_connected = False
        base_dir = Path(__file__).resolve().parents[2]
        self._qml_path = base_dir / "qml" / "GalleryGrid.qml"

        surface_format = QSurfaceFormat()
        surface_format.setAlphaBufferSize(0)
        self.setFormat(surface_format)

        self.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        base_color = self.palette().color(QPalette.ColorRole.Window)
        self.setClearColor(base_color)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        engine = self.engine()
        if engine is not None:
            engine.addImportPath(str(self._qml_path.parent))
        self._engine = engine

    # ------------------------------------------------------------------
    # Model + selection handling
    # ------------------------------------------------------------------
    def setModel(self, model: "AssetModel") -> None:  # type: ignore[override]
        self._model = model
        self._selection_model = QItemSelectionModel(model, self)
        self._selection_model.selectionChanged.connect(self._sync_selection_roles)
        self._selection_model.currentChanged.connect(self._sync_current_role)

        if self._engine is not None:
            self._engine.rootContext().setContextProperty("assetModel", model)
            provider_model = self._resolve_source_model(model)
            if provider_model is not None:
                self._engine.addImageProvider("thumbnails", _ThumbnailImageProvider(provider_model))

        if not self._qml_loaded:
            self._load_qml()
        else:
            self._connect_qml_signals()
            self._sync_theme_to_qml()

    def selectionModel(self) -> Optional[QItemSelectionModel]:  # type: ignore[override]
        return self._selection_model

    def clearSelection(self) -> None:  # type: ignore[override]
        if self._selection_model is None:
            return
        self._selection_model.clearSelection()
        self._sync_all_selection_roles()

    def selection_mode_active(self) -> bool:
        """Return ``True`` when multi-selection mode is enabled."""

        return self._selection_mode_enabled

    def set_selection_mode_enabled(self, enabled: bool) -> None:
        """Switch between single and multi-selection modes."""

        desired = bool(enabled)
        if self._selection_mode_enabled == desired:
            return
        self._selection_mode_enabled = desired
        root = self.rootObject()
        if root is not None:
            root.setProperty("selectionMode", desired)

    # ------------------------------------------------------------------
    # External drop support
    # ------------------------------------------------------------------
    def configure_external_drop(self, *, handler=None, validator=None) -> None:
        """Register drop callbacks used by :class:`DragDropController`."""

        self._external_drop_handler = handler
        self._external_drop_validator = validator
        self.setAcceptDrops(self._external_drop_handler is not None)

    # ------------------------------------------------------------------
    # Theme handling
    # ------------------------------------------------------------------
    def apply_theme(self, colors: ThemeColors) -> None:
        """Apply theme colors to both the widget and underlying QML."""

        self._theme_colors = colors

        # Force opaque color
        opaque_bg = QColor(colors.window_background)
        opaque_bg.setAlpha(255)

        self._apply_background_color(opaque_bg)
        self._sync_theme_to_qml()
        self.setClearColor(opaque_bg)

    def paintEvent(self, event) -> None:
        """Manually fill background before QML rendering to prevent transparency issues on Windows."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Window))
        painter.end()
        super().paintEvent(event)

    def _apply_background_color(self, color: QColor) -> None:
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, color)
        palette.setColor(QPalette.ColorRole.Base, color)
        self.setPalette(palette)
        self.setAutoFillBackground(True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _load_qml(self) -> None:
        if self._engine is None:
            return
        self.setSource(QUrl.fromLocalFile(str(self._qml_path)))
        self._qml_loaded = True
        self._qml_signals_connected = False
        root = self.rootObject()
        if root is not None and self._theme_colors is None:
            base_color = self.palette().color(QPalette.ColorRole.Window)
            self.setClearColor(base_color)
            root.setProperty("backgroundColor", base_color)
            root.setProperty("itemBackgroundColor", base_color)
        self._connect_qml_signals()
        self._sync_theme_to_qml()

    def _connect_qml_signals(self) -> None:
        root = self.rootObject()
        if root is None or self._qml_signals_connected:
            return
        root.itemClicked.connect(self._on_qml_item_clicked, Qt.ConnectionType.UniqueConnection)
        root.itemDoubleClicked.connect(
            self._on_qml_item_double_clicked, Qt.ConnectionType.UniqueConnection
        )
        root.currentIndexChanged.connect(
            self._on_qml_current_changed, Qt.ConnectionType.UniqueConnection
        )
        root.visibleRowsChanged.connect(
            self.visibleRowsChanged, Qt.ConnectionType.UniqueConnection
        )
        root.showContextMenu.connect(
            self._on_qml_context_menu, Qt.ConnectionType.UniqueConnection
        )
        root.filesDropped.connect(self._on_qml_files_dropped, Qt.ConnectionType.UniqueConnection)
        self._qml_signals_connected = True

    def _on_qml_item_clicked(self, row: int, modifiers: int) -> None:
        index = self._index_for_row(row)
        if not index.isValid() or self._selection_model is None:
            return

        modifiers_enum = Qt.KeyboardModifier(modifiers)
        if self._selection_mode_enabled or modifiers_enum & Qt.KeyboardModifier.ControlModifier:
            command = (
                QItemSelectionModel.SelectionFlag.Deselect
                if self._selection_model.isSelected(index)
                else QItemSelectionModel.SelectionFlag.Select
            )
            self._selection_model.select(index, command | QItemSelectionModel.SelectionFlag.Current)
        else:
            self._selection_model.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Select,
            )
        self.itemClicked.emit(index)

    def _on_qml_item_double_clicked(self, row: int) -> None:
        index = self._index_for_row(row)
        if index.isValid():
            self.itemDoubleClicked.emit(index)

    def _on_qml_current_changed(self, row: int) -> None:
        if self._selection_model is None:
            return
        index = self._index_for_row(row)
        if index.isValid():
            self._selection_model.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )

    def _on_qml_context_menu(self, row: int, global_x: int, global_y: int) -> None:
        index = self._index_for_row(row)
        self.contextMenuRequested.emit(index, QPoint(global_x, global_y))

    def _on_qml_files_dropped(self, urls: Iterable[str]) -> None:
        if self._external_drop_handler is None:
            return

        paths = []
        for url in urls:
            qurl = QUrl(url)
            if qurl.isLocalFile():
                paths.append(Path(qurl.toLocalFile()))
        if not paths:
            return
        if self._external_drop_validator is not None and not self._external_drop_validator(paths):
            return
        self._external_drop_handler(paths)

    def _sync_selection_roles(self, selected: QItemSelection, deselected: QItemSelection) -> None:
        if self._model is None:
            return
        for index in deselected.indexes():
            self._model.setData(index, False, Roles.IS_SELECTED)
        for index in selected.indexes():
            self._model.setData(index, True, Roles.IS_SELECTED)

    def _sync_all_selection_roles(self) -> None:
        if self._selection_model is None or self._model is None:
            return
        rows = self._selection_model.model().rowCount()
        for row in range(rows):
            index = self._selection_model.model().index(row, 0)
            self._model.setData(index, self._selection_model.isSelected(index), Roles.IS_SELECTED)

    def _sync_current_role(self, current: QModelIndex, previous: QModelIndex) -> None:
        if self._model is None:
            return
        if previous.isValid():
            self._model.setData(previous, False, Roles.IS_CURRENT)
        if current.isValid():
            self._model.setData(current, True, Roles.IS_CURRENT)

    def _index_for_row(self, row: int) -> QModelIndex:
        if self._model is None:
            return QModelIndex()
        index = self._model.index(row, 0)
        return index if index.isValid() else QModelIndex()

    def _resolve_source_model(self, model: "AssetModel") -> Optional["AssetListModel"]:
        source = getattr(model, "source_model", None)
        if callable(source):
            try:
                resolved = source()
            except Exception:
                return None
            return resolved
        return None

    def _sync_theme_to_qml(self) -> None:
        if self._theme_colors is None:
            return
        root = self.rootObject()
        if root is None:
            return

        colors = self._theme_colors
        item_bg = colors.window_background.darker(115 if colors.is_dark else 105)

        self.setClearColor(colors.window_background)
        root.setProperty("backgroundColor", colors.window_background)
        root.setProperty("itemBackgroundColor", item_bg)
        root.setProperty("selectionBorderColor", colors.accent_color)
        root.setProperty("currentBorderColor", colors.text_primary)


__all__ = ["GalleryQuickWidget"]
