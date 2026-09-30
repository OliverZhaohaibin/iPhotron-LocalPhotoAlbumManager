from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt

from iPhoto.gui.ui.models.roles import Roles
from iPhoto.gui.ui.models.thumbnail_surface_proxy_model import ThumbnailSurfaceProxyModel


class _Source(QAbstractListModel):
    def __init__(self) -> None:
        super().__init__()
        self.calls = []

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else 1

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        return "source" if index.isValid() else None

    def tile_snapshot(self, row: int, surface_id: str):
        self.calls.append(("snapshot", row, surface_id))
        return "filmstrip-snapshot"

    def full_thumbnail(self, row: int, surface_id: str):
        self.calls.append(("full", row, surface_id))
        return QSize(256, 256)


def test_proxy_resolves_thumbnail_roles_for_its_surface() -> None:
    source = _Source()
    proxy = ThumbnailSurfaceProxyModel("filmstrip")
    proxy.setSourceModel(source)
    index = proxy.index(0, 0)

    assert proxy.data(index, Roles.TILE_SNAPSHOT) == "filmstrip-snapshot"
    assert proxy.data(index, Qt.ItemDataRole.DecorationRole) == QSize(256, 256)
    assert source.calls == [
        ("snapshot", 0, "filmstrip"),
        ("full", 0, "filmstrip"),
    ]
