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


def test_open_toggle_initialises_recognition_before_publishing_presentation() -> None:
    calls: list[str] = []
    panel = Mock(isVisible=Mock(return_value=False))
    coordinator = LocationInfoCoordinator.__new__(LocationInfoCoordinator)
    coordinator._window = SimpleNamespace(ui=SimpleNamespace(info_panel=panel))
    coordinator._panel = panel
    coordinator._detail = Mock(
        toggle_info_panel=Mock(side_effect=lambda: calls.append("toggle")),
    )
    coordinator._recognition_provider = Mock(
        side_effect=lambda: calls.append("recognition") or object()
    )
    coordinator._recognition_initialized = False
    coordinator._recognition_initialization_attempted = False
    coordinator._map_runtime_getter = Mock(return_value=None)

    LocationInfoCoordinator.toggle(coordinator)

    assert calls == ["recognition", "toggle"]
    coordinator._recognition_provider.assert_called_once_with()


def test_opening_panel_initialises_recognition_once_before_toggle() -> None:
    coordinator = LocationInfoCoordinator.__new__(LocationInfoCoordinator)
    panel = Mock()
    coordinator._panel = panel
    coordinator._recognition_provider = Mock(return_value=object())
    coordinator._recognition_initialized = False
    coordinator._recognition_initialization_attempted = False

    LocationInfoCoordinator._initialize_recognition_once(coordinator, panel)
    LocationInfoCoordinator._initialize_recognition_once(coordinator, panel)

    coordinator._recognition_provider.assert_called_once_with()
    assert coordinator._recognition_initialized


def test_failed_recognition_initialisation_is_not_retried_on_reopen() -> None:
    coordinator = LocationInfoCoordinator.__new__(LocationInfoCoordinator)
    panel = Mock()
    coordinator._panel = panel
    coordinator._recognition_provider = Mock(side_effect=RuntimeError("unavailable"))
    coordinator._recognition_initialized = False
    coordinator._recognition_initialization_attempted = False

    LocationInfoCoordinator._initialize_recognition_once(coordinator, panel)
    LocationInfoCoordinator._initialize_recognition_once(coordinator, panel)

    coordinator._recognition_provider.assert_called_once_with()
    panel.set_face_actions_enabled.assert_called_once_with(False)
