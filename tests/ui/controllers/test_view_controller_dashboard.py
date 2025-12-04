
from unittest.mock import MagicMock
from PySide6.QtWidgets import QStackedWidget, QWidget

from iPhoto.gui.ui.controllers.view_controller import ViewController
from iPhoto.gui.ui.widgets.albums_dashboard import AlbumsDashboard

def test_show_albums_dashboard_triggers_refresh(qtbot):
    """
    Test that showing the albums dashboard triggers a refresh of the dashboard content.
    This mimics the user navigating back to the Album Dashboard.
    """
    # Mock the dependencies
    view_stack = MagicMock(spec=QStackedWidget)
    gallery_page = MagicMock(spec=QWidget)
    detail_page = MagicMock(spec=QWidget)

    # Mock the AlbumsDashboard
    # We need it to be a QWidget so type checks pass, but we want to track the refresh call
    albums_dashboard_page = MagicMock(spec=AlbumsDashboard)

    # Setup the controller
    controller = ViewController(
        view_stack=view_stack,
        gallery_page=gallery_page,
        detail_page=detail_page,
        albums_dashboard_page=albums_dashboard_page
    )

    # Simulate showing the dashboard
    controller.show_albums_dashboard()

    # Check if the view stack was updated
    view_stack.setCurrentWidget.assert_called_with(albums_dashboard_page)

    # Check if refresh was called
    # This assertion is expected to fail before the fix
    albums_dashboard_page.refresh.assert_called_once()
