"""Proxy model that injects spacer rows for the filmstrip view."""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractProxyModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    QSize,
)

from .proxy_mapping import map_to_root_source, root_source_model
from .roles import Roles


class SpacerProxyModel(QAbstractProxyModel):
    """Wrap an asset model and expose leading/trailing spacer rows."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._spacer_size = QSize(0, 0)
        self._layout_indexes: list[tuple[QModelIndex, QPersistentModelIndex]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_spacer_width(self, width: int) -> None:
        """Update spacer width and notify views when it changes."""

        width = max(0, width)
        if self._spacer_size.width() == width:
            return

        self._spacer_size.setWidth(width)

        # The leading and trailing spacer rows are the only items whose
        # geometry depends on this width. Instead of resetting the entire
        # model (which would force every view to throw away caches and
        # re-query all data), emit targeted ``dataChanged`` signals so
        # views simply refresh the two spacer delegates. This keeps
        # navigation responsive even for very large albums.
        source = self.sourceModel()
        if source is None:
            return

        source_rows = source.rowCount()
        if source_rows <= 0:
            return

        first_idx = self.index(0, 0)
        last_idx = self.index(source_rows + 1, 0)
        roles = [Qt.ItemDataRole.SizeHintRole]
        self.dataChanged.emit(first_idx, first_idx, roles)
        self.dataChanged.emit(last_idx, last_idx, roles)

    # ------------------------------------------------------------------
    # QAbstractProxyModel overrides
    # ------------------------------------------------------------------
    def setSourceModel(self, source_model) -> None:  # type: ignore[override]
        if source_model is self:
            raise ValueError(
                "Circular reference detected: SpacerProxyModel cannot be its own source."
            )

        # Detect indirect cycles if the source is another proxy that points back to us.
        # This isn't exhaustive (doesn't walk the full chain) but catches the most
        # common mistake of `proxy.setSourceModel(proxy_that_wraps_proxy)`.
        candidate = source_model
        while hasattr(candidate, "sourceModel"):
            candidate = candidate.sourceModel()
            if candidate is self:
                raise ValueError(
                    "Circular reference detected: "
                    "SpacerProxyModel source chain leads back to self."
                )

        previous = self.sourceModel()
        if previous is source_model:
            return
        source_signals = (
            ("modelAboutToBeReset", self.beginResetModel),
            ("modelReset", self.endResetModel),
            ("rowsAboutToBeInserted", self._begin_source_insert),
            ("rowsInserted", self.endInsertRows),
            ("rowsAboutToBeRemoved", self._begin_source_remove),
            ("rowsRemoved", self.endRemoveRows),
            ("layoutAboutToBeChanged", self._begin_source_layout),
            ("layoutChanged", self._finish_source_layout),
            ("dataChanged", self._handle_source_data_changed),
        )
        if previous is not None:
            for name, handler in source_signals:
                getattr(previous, name).disconnect(handler)
            signal = getattr(root_source_model(previous), "persistentRowsRequested", None)
            if signal is not None:
                signal.disconnect(self._collect_persistent_source_rows)

        self.beginResetModel()
        super().setSourceModel(source_model)
        self.endResetModel()

        if source_model is not None:
            for name, handler in source_signals:
                getattr(source_model, name).connect(handler)
            signal = getattr(root_source_model(source_model), "persistentRowsRequested", None)
            if signal is not None:
                signal.connect(self._collect_persistent_source_rows)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        source = self.sourceModel()
        if source is None:
            return 0
        count = source.rowCount(parent)
        return count + 2 if count > 0 else 0

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        source = self.sourceModel()
        return source.columnCount(parent) if source is not None else 0

    def mapToSource(self, proxy_index: QModelIndex) -> QModelIndex:  # noqa: N802
        source = self.sourceModel()
        if source is None or not proxy_index.isValid():
            return QModelIndex()

        # Runtime safety: if source somehow became self (or a wrapper leading to self),
        # prevent infinite recursion and crash. This can happen if the model graph
        # is mutated dynamically in ways `setSourceModel` couldn't catch initially.
        if source is self:
            return QModelIndex()

        row = proxy_index.row()
        count = source.rowCount()
        if not (1 <= row <= count):
            return QModelIndex()
        return source.index(row - 1, proxy_index.column())

    def mapFromSource(self, source_index: QModelIndex) -> QModelIndex:  # noqa: N802
        if not source_index.isValid():
            return QModelIndex()
        return self.index(source_index.row() + 1, source_index.column())

    def index(
        self, row: int, column: int, parent: QModelIndex = QModelIndex()
    ) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, _index: QModelIndex) -> QModelIndex:  # noqa: N802
        return QModelIndex()

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None

        source = self.sourceModel()
        if source is None:
            return None

        row = index.row()
        last_row = self.rowCount() - 1
        if row in {0, last_row} and last_row >= 0:
            if role == Roles.IS_SPACER:
                return True
            if role in (Qt.ItemDataRole.SizeHintRole, Qt.SizeHintRole):
                return QSize(self._spacer_size.width(), self._spacer_size.height())
            if role == Qt.ItemDataRole.DisplayRole:
                return None
            return None

        source_index = self.mapToSource(index)
        if not source_index.isValid():
            return None
        return source.data(source_index, role)

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:  # noqa: N802
        if not index.isValid():
            return Qt.NoItemFlags
        if bool(self.data(index, Roles.IS_SPACER)):
            return Qt.NoItemFlags
        source_index = self.mapToSource(index)
        source = self.sourceModel()
        if source is None or not source_index.isValid():
            return Qt.NoItemFlags
        return source.flags(source_index)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _begin_source_insert(self, _parent: QModelIndex, first: int, last: int) -> None:
        if not self.rowCount():
            self.beginInsertRows(QModelIndex(), 0, last + 2)
        else:
            self.beginInsertRows(QModelIndex(), first + 1, last + 1)

    def _begin_source_remove(self, _parent: QModelIndex, first: int, last: int) -> None:
        if last - first + 1 == self.sourceModel().rowCount():
            self.beginRemoveRows(QModelIndex(), 0, last + 2)
        else:
            self.beginRemoveRows(QModelIndex(), first + 1, last + 1)

    def _collect_persistent_source_rows(self, rows: set[int]) -> None:
        for proxy_index in self.persistentIndexList():
            source_index = map_to_root_source(proxy_index)
            if source_index.isValid():
                rows.add(source_index.row())

    def _begin_source_layout(self) -> None:
        self.layoutAboutToBeChanged.emit()
        self._layout_indexes = [
            (index, QPersistentModelIndex(source_index))
            for index in self.persistentIndexList()
            if (source_index := self.mapToSource(index)).isValid()
        ]

    def _finish_source_layout(self) -> None:
        self.changePersistentIndexList(
            [index for index, _source in self._layout_indexes],
            [self.mapFromSource(QModelIndex(source)) for _index, source in self._layout_indexes],
        )
        self._layout_indexes.clear()
        self.layoutChanged.emit()

    def _handle_source_data_changed(
        self,
        top_left: QModelIndex,
        bottom_right: QModelIndex,
        roles: list[int] | None = None,
    ) -> None:
        """Forward data changes from the source model to the proxy."""

        if not top_left.isValid() or not bottom_right.isValid():
            return

        proxy_top_left = self.mapFromSource(top_left)
        proxy_bottom_right = self.mapFromSource(bottom_right)

        if not proxy_top_left.isValid() or not proxy_bottom_right.isValid():
            return

        # ``dataChanged`` signal signature requires roles to be a list or empty.
        # Passing None directly can cause issues with some Qt bindings/versions.
        safe_roles = roles if roles is not None else []
        self.dataChanged.emit(proxy_top_left, proxy_bottom_right, safe_roles)
