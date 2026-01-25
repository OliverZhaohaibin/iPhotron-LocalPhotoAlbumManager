import pytest
from unittest.mock import MagicMock
from PySide6.QtCore import QModelIndex, Signal, QObject, QAbstractListModel
from PySide6.QtWidgets import QApplication

from src.iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from src.iPhoto.gui.ui.models.spacer_proxy_model import SpacerProxyModel
from src.iPhoto.gui.ui.models.roles import Roles

class MockViewModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self._items = range(10) # 10 items
        self.current_row = -1

    def rowCount(self, parent=QModelIndex()):
        return len(self._items)

    def columnCount(self, parent=QModelIndex()):
        return 1

    def index(self, row, column, parent=QModelIndex()):
        return self.createIndex(row, column)

    def data(self, index, role):
        if role == Roles.ABS:
            return f"/path/to/asset_{index.row()}.jpg"
        return None

    def set_current_row(self, row):
        self.current_row = row

class MockFilmstripView(QObject):
    nextItemRequested = Signal()
    prevItemRequested = Signal()
    itemClicked = Signal(QModelIndex)

    def __init__(self):
        super().__init__()
        self._model = None
        self._selection_model = MagicMock()
        self._visible = True

    def setModel(self, model):
        self._model = model

    def model(self):
        return self._model

    def selectionModel(self):
        return self._selection_model

    def center_on_index(self, index):
        pass

    def setVisible(self, visible):
        self._visible = visible

@pytest.fixture
def mock_deps(qtbot):
    # Mock dependencies for PlaybackCoordinator
    player_bar = MagicMock()
    player_view = MagicMock()
    router = MagicMock()

    asset_vm = MockViewModel()

    zoom_slider = MagicMock()
    zoom_in = MagicMock()
    zoom_out = MagicMock()
    zoom_widget = MagicMock()

    fav_btn = MagicMock()
    info_btn = MagicMock()
    rot_btn = MagicMock()
    edit_btn = MagicMock()
    share_btn = MagicMock()

    filmstrip_view = MockFilmstripView()
    toggle_action = MagicMock()
    settings = MagicMock()

    return {
        "player_bar": player_bar,
        "player_view": player_view,
        "router": router,
        "asset_vm": asset_vm,
        "zoom_slider": zoom_slider,
        "zoom_in": zoom_in,
        "zoom_out": zoom_out,
        "zoom_widget": zoom_widget,
        "fav_btn": fav_btn,
        "info_btn": info_btn,
        "rot_btn": rot_btn,
        "edit_btn": edit_btn,
        "share_btn": share_btn,
        "filmstrip_view": filmstrip_view,
        "toggle_action": toggle_action,
        "settings": settings,
    }

def test_playback_coordinator_proxy_mapping(qtbot, mock_deps):
    # Setup Proxy Model
    asset_vm = mock_deps["asset_vm"]
    filmstrip_view = mock_deps["filmstrip_view"]

    proxy_model = SpacerProxyModel()
    proxy_model.setSourceModel(asset_vm)
    filmstrip_view.setModel(proxy_model)

    coordinator = PlaybackCoordinator(
        mock_deps["player_bar"],
        mock_deps["player_view"],
        mock_deps["router"],
        asset_vm,
        mock_deps["zoom_slider"],
        mock_deps["zoom_in"],
        mock_deps["zoom_out"],
        mock_deps["zoom_widget"],
        mock_deps["fav_btn"],
        mock_deps["info_btn"],
        mock_deps["rot_btn"],
        mock_deps["edit_btn"],
        mock_deps["share_btn"],
        filmstrip_view,
        mock_deps["toggle_action"],
        mock_deps["settings"]
    )

    # 1. Test Sync: Source Row -> Proxy Index Selection
    source_row = 5
    coordinator.play_asset(source_row)

    # Verify mapping: Proxy row should be source_row + 1 (due to leading spacer)
    expected_proxy_row = source_row + 1

    # Check selection call
    selection_call = filmstrip_view.selectionModel().setCurrentIndex.call_args
    assert selection_call is not None
    selected_idx = selection_call[0][0]
    assert selected_idx.isValid()
    assert selected_idx.model() == proxy_model
    assert selected_idx.row() == expected_proxy_row

    # 2. Test Click: Proxy Index -> Source Row Playback
    # Simulate clicking item at row 3 in proxy (should be row 2 in source)
    clicked_proxy_row = 3
    expected_source_row = 2

    proxy_idx = proxy_model.index(clicked_proxy_row, 0)

    # Spy on play_asset to verify recursive call (or just check current_row state)
    # Since play_asset updates state, we can check coordinator.current_row()

    filmstrip_view.itemClicked.emit(proxy_idx)

    assert coordinator.current_row() == expected_source_row

def test_playback_coordinator_proxy_spacer_click(qtbot, mock_deps):
    # Test that clicking a spacer does nothing
    asset_vm = mock_deps["asset_vm"]
    filmstrip_view = mock_deps["filmstrip_view"]

    proxy_model = SpacerProxyModel()
    proxy_model.setSourceModel(asset_vm)
    filmstrip_view.setModel(proxy_model)

    coordinator = PlaybackCoordinator(
        mock_deps["player_bar"],
        mock_deps["player_view"],
        mock_deps["router"],
        asset_vm,
        mock_deps["zoom_slider"],
        mock_deps["zoom_in"],
        mock_deps["zoom_out"],
        mock_deps["zoom_widget"],
        mock_deps["fav_btn"],
        mock_deps["info_btn"],
        mock_deps["rot_btn"],
        mock_deps["edit_btn"],
        mock_deps["share_btn"],
        filmstrip_view,
        mock_deps["toggle_action"],
        mock_deps["settings"]
    )

    # Initially at row -1
    assert coordinator.current_row() == -1

    # Click on the first spacer (row 0)
    spacer_idx = proxy_model.index(0, 0)
    filmstrip_view.itemClicked.emit(spacer_idx)

    # Should still be -1 (no change)
    assert coordinator.current_row() == -1
