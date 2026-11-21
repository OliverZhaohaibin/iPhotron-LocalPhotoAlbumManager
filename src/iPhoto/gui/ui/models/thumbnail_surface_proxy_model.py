"""Surface-specific, memory-only thumbnail presentation proxy."""

from __future__ import annotations

from PySide6.QtCore import QIdentityProxyModel, QModelIndex, Qt

from .roles import Roles


class ThumbnailSurfaceProxyModel(QIdentityProxyModel):
    """Resolve thumbnail roles with one surface's display bucket."""

    def __init__(self, surface_id: str, parent=None) -> None:
        super().__init__(parent)
        self._surface_id = str(surface_id)

    @property
    def surface_id(self) -> str:
        return self._surface_id

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        source = self.sourceModel()
        source_index = self.mapToSource(index)
        if source is None or not source_index.isValid():
            return None
        role_int = int(role)
        if role_int == Roles.TILE_SNAPSHOT:
            getter = getattr(source, "tile_snapshot", None)
            if callable(getter):
                return getter(source_index.row(), self._surface_id)
        if role_int == int(Qt.ItemDataRole.DecorationRole):
            getter = getattr(source, "full_thumbnail", None)
            if callable(getter):
                return getter(source_index.row(), self._surface_id)
        return source.data(source_index, role)


__all__ = ["ThumbnailSurfaceProxyModel"]
