from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for coordinator tests", exc_type=ImportError)

from iPhoto.gui.coordinators.location_info_coordinator import LocationInfoCoordinator


def test_visible_panel_toggle_uses_close_fast_path() -> None:
    coordinator = LocationInfoCoordinator.__new__(LocationInfoCoordinator)
    panel = Mock(isVisible=Mock(return_value=True))
    coordinator._window = SimpleNamespace(ui=SimpleNamespace(info_panel=panel))
    coordinator._detail = Mock(toggle_info_panel=Mock())
    coordinator._recognition_provider = Mock()
    coordinator._map_runtime_getter = Mock()

    LocationInfoCoordinator.toggle(coordinator)

    coordinator._detail.toggle_info_panel.assert_called_once_with()
    coordinator._recognition_provider.assert_not_called()
    coordinator._map_runtime_getter.assert_not_called()


def test_presented_panel_initialises_recognition_once_and_refreshes_faces() -> None:
    coordinator = LocationInfoCoordinator.__new__(LocationInfoCoordinator)
    panel = Mock(isVisible=Mock(return_value=True))
    coordinator._panel = panel
    coordinator._detail = Mock(refresh_info_panel_faces=Mock())
    coordinator._recognition_provider = Mock(return_value=object())
    coordinator._recognition_initialized = False

    LocationInfoCoordinator._handle_panel_presented(coordinator)
    LocationInfoCoordinator._handle_panel_presented(coordinator)

    coordinator._recognition_provider.assert_called_once_with()
    assert coordinator._detail.refresh_info_panel_faces.call_count == 2


def test_hidden_panel_does_not_initialise_recognition() -> None:
    coordinator = LocationInfoCoordinator.__new__(LocationInfoCoordinator)
    coordinator._panel = Mock(isVisible=Mock(return_value=False))
    coordinator._detail = Mock(refresh_info_panel_faces=Mock())
    coordinator._recognition_provider = Mock(return_value=object())
    coordinator._recognition_initialized = False

    LocationInfoCoordinator._handle_panel_presented(coordinator)

    coordinator._recognition_provider.assert_not_called()
    coordinator._detail.refresh_info_panel_faces.assert_not_called()
