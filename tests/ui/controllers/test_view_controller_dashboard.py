
from unittest.mock import MagicMock
from PySide6.QtWidgets import QStackedWidget, QWidget

from src.iPhoto.gui.ui.controllers.view_controller import ViewController
from src.iPhoto.gui.ui.widgets.albums_dashboard import AlbumsDashboard

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
    albums_dashboard_page.refresh.assert_called_once()

def test_show_albums_dashboard_refreshes_when_already_shown(qtbot):
    """
    Test that refresh is called even if the dashboard is already the current widget.
    This ensures stale data is updated on repeated navigation.
    """
    view_stack = MagicMock(spec=QStackedWidget)
    albums_dashboard_page = MagicMock(spec=AlbumsDashboard)

    # Make the dashboard already the current widget
    view_stack.currentWidget.return_value = albums_dashboard_page

    controller = ViewController(
        view_stack=view_stack,
        gallery_page=MagicMock(spec=QWidget),
        detail_page=MagicMock(spec=QWidget),
        albums_dashboard_page=albums_dashboard_page
    )

    controller.show_albums_dashboard()

    # Refresh should still be called even though we didn't switch widgets
    albums_dashboard_page.refresh.assert_called_once()
