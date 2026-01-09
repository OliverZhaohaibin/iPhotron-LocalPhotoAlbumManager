import pytest
from PySide6.QtCore import QAbstractListModel, Qt, QModelIndex
from PySide6.QtWidgets import QApplication
from src.iPhoto.gui.ui.widgets.filmstrip_view import FilmstripView
from src.iPhoto.gui.ui.models.roles import Roles

class MockAssetModel(QAbstractListModel):
    def __init__(self, count=1000):
        super().__init__()
        self._count = count
        self.data_calls = 0

    def rowCount(self, parent=QModelIndex()):
        return self._count

    def data(self, index, role=Qt.DisplayRole):
        if role == Roles.IS_CURRENT:
            self.data_calls += 1
            # Return True for the last item to force worst-case search
            return index.row() == self._count - 1
        if role == Roles.IS_SPACER:
            return False
        return None

    def set_spacer_width(self, width):
        pass

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app

def test_filmstrip_performance_reproduction(qapp):
    model = MockAssetModel(count=1000)
    view = FilmstripView()
    view.setModel(model)
    view.resize(1000, 200)

    # Reset counter
    model.data_calls = 0

    # Trigger refresh_spacers which calls _current_item_width
    view.refresh_spacers()

    # Assert that we DO NOT iterate over the whole model.
    # Before the fix, this will fail because data_calls will be ~1000.
    # After the fix, it should be very small (e.g., 0 or 1 depending on selection logic).
    assert model.data_calls < 50, f"Performance regression: {model.data_calls} calls to data()"
