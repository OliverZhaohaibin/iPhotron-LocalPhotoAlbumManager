
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMainWindow, QApplication

from iPhotos.src.iPhoto.gui.ui.controllers.main_controller import MainController
from iPhotos.src.iPhoto.appctx import AppContext

@pytest.fixture
def qapp():
    if not QApplication.instance():
        return QApplication([])
    return QApplication.instance()

def test_main_controller_lazy_init(qapp):
    # Use real QMainWindow as parent for QObject compatibility
    window = QMainWindow()

    # Attach mocks for attributes expected by MainController
    window.ui = MagicMock()
    window.window_manager = MagicMock()

    context = MagicMock(spec=AppContext)
    context.facade = MagicMock()
    context.library = MagicMock() # Ensure library is mocked

    # Instantiate MainController
    controller = MainController(window, context)

    # Verify attributes are None initially
    assert controller._data is None
    assert controller._navigation is None

    # Verify safety checks work before boot
    assert controller.is_edit_view_active() is False
    assert controller.edit_controller() is None

    # Boot services
    # Patch the heavy classes to avoid real instantiation and side effects
    with patch("iPhotos.src.iPhoto.gui.ui.controllers.main_controller.DataManager") as MockDataManager, \
         patch("iPhotos.src.iPhoto.gui.ui.controllers.main_controller.DialogController") as MockDialogController, \
         patch("iPhotos.src.iPhoto.gui.ui.controllers.main_controller.StatusBarController") as MockStatusBarController, \
         patch("iPhotos.src.iPhoto.gui.ui.controllers.main_controller.ViewControllerManager") as MockViewControllerManager, \
         patch("iPhotos.src.iPhoto.gui.ui.controllers.main_controller.NavigationController") as MockNavigationController, \
         patch("iPhotos.src.iPhoto.gui.ui.controllers.main_controller.InteractionManager") as MockInteractionManager:

         # Setup mocks to return objects that have expected methods to avoid crashes during assignment
         mock_data = MockDataManager.return_value
         mock_view_manager = MockViewControllerManager.return_value
         mock_interaction = MockInteractionManager.return_value

         # Mock return values for cached shortcuts used in boot_services
         mock_data.playlist.return_value = MagicMock()
         mock_data.asset_model.return_value = MagicMock()
         mock_data.filmstrip_model.return_value = MagicMock()
         mock_data.media.return_value = MagicMock() # for _connect_signals calling self._data.media()

         mock_view_manager.view_controller.return_value = MagicMock()
         mock_view_manager.detail_ui.return_value = MagicMock()
         mock_view_manager.map_controller.return_value = MagicMock()
         mock_view_manager.edit_controller.return_value = MagicMock()

         mock_interaction.playback.return_value = MagicMock()
         mock_interaction.state_manager.return_value = MagicMock()
         mock_interaction.selection.return_value = MagicMock()
         mock_interaction.preview.return_value = MagicMock()

         controller.boot_services()

         # Verify attributes are populated
         assert controller._data is not None
         assert controller._navigation is not None
         assert controller._interaction is not None

         # Verify boot_services called the constructors
         MockDataManager.assert_called_once()
         MockNavigationController.assert_called_once()

         # Verify library initialization is triggered
         context.initialize_library.assert_called_once()

def test_app_context_validate_recent_albums():
    # Create AppContext manually or mock it
    # We can test validate_recent_albums logic in isolation

    context = AppContext()
    # Mock settings
    context.settings = MagicMock()
    context.settings.get.return_value = []

    # Setup recent_albums with existing and non-existing paths
    path_exists = MagicMock(spec=Path)
    path_exists.exists.return_value = True
    path_exists.__str__.return_value = "/path/exists"

    path_missing = MagicMock(spec=Path)
    path_missing.exists.return_value = False
    path_missing.__str__.return_value = "/path/missing"

    context.recent_albums = [path_exists, path_missing]

    context.validate_recent_albums()

    assert context.recent_albums == [path_exists]
    context.settings.set.assert_called_with("last_open_albums", ["/path/exists"])
