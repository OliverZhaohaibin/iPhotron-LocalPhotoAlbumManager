"""Unit tests for :mod:`iPhoto.gui.ui.controllers.share_controller`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for share controller tests",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtWidgets",
    reason="Qt widgets are required for share controller tests",
    exc_type=ImportError,
)
pytest.importorskip(
    "PySide6.QtGui",
    reason="Qt GUI module is required for share controller tests",
    exc_type=ImportError,
)

from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtGui import QAction, QActionGroup

from iPhoto.gui.ui.controllers.share_controller import ShareController


@pytest.fixture()
def qapp() -> QApplication:
    """Provide a QApplication for Qt widgets created during the tests."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class StubSettings:
    """Minimal settings backend used to capture read and write operations."""

    def __init__(self, value: Optional[str] = None) -> None:
        self._value = value

    def get(self, key: str, default: str) -> str:
        """Return the stored preference or ``default`` when none exists."""

        return self._value if self._value is not None else default

    def set(self, key: str, value: str) -> None:
        """Persist the value so subsequent reads can observe it."""

        self._value = value


class StubPathProvider:
    """Simple current-path provider used by the controller tests."""

    def __init__(self, path: Optional[Path]) -> None:
        self._path = path

    def __call__(self) -> Optional[Path]:
        return self._path


class StubStatusBar:
    """Collect status messages so tests can assert on user feedback."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, int]] = []

    def show_message(self, message: str, timeout: int) -> None:
        self.messages.append((message, timeout))


class StubToast:
    """Notification toast stub that records the last presented text."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def show_toast(self, message: str) -> None:
        self.messages.append(message)


@pytest.fixture()
def controller_factory(qapp: QApplication):
    """Return a helper that creates a :class:`ShareController` for tests."""

    # The ``qapp`` fixture ensures a QApplication exists before any QWidget is
    # constructed.  Without this dependency the helper would instantiate
    # buttons prior to QApplication initialisation which raises runtime errors
    # in Qt.

    def factory(
        *,
        settings: StubSettings,
        current_path_provider: StubPathProvider,
    ) -> ShareController:
        status_bar = StubStatusBar()
        toast = StubToast()
        share_button = QPushButton("Share")
        action_group = QActionGroup(share_button)
        copy_file_action = QAction("Copy File", share_button)
        copy_path_action = QAction("Copy Path", share_button)
        reveal_action = QAction("Reveal", share_button)
        copy_file_action.setCheckable(True)
        copy_path_action.setCheckable(True)
        reveal_action.setCheckable(True)
        action_group.addAction(copy_file_action)
        action_group.addAction(copy_path_action)
        action_group.addAction(reveal_action)

        controller = ShareController(
            settings=settings,
            current_path_provider=current_path_provider,
            status_bar=status_bar,
            notification_toast=toast,
            share_button=share_button,
            share_action_group=action_group,
            copy_file_action=copy_file_action,
            copy_path_action=copy_path_action,
            reveal_action=reveal_action,
        )
        return controller

    return factory


def test_restore_preference_checks_expected_action(controller_factory, qapp: QApplication) -> None:
    """Restoring the preference should check the matching QAction."""

    settings = StubSettings("copy_path")
    path_provider = StubPathProvider(None)
    controller = controller_factory(
        settings=settings,
        current_path_provider=path_provider,
    )

    controller.restore_preference()

    assert controller._copy_path_action.isChecked()


def test_share_without_selection_shows_status_message(controller_factory, qapp: QApplication) -> None:
    """Clicking the share button without a selection should inform the user."""

    settings = StubSettings("reveal_file")
    path_provider = StubPathProvider(None)
    controller = controller_factory(
        settings=settings,
        current_path_provider=path_provider,
    )

    controller._share_button.click()

    assert controller._status_bar.messages == [("No item selected to share.", 3000)]


def test_share_uses_preferred_action(
    controller_factory, qapp: QApplication, mocker, tmp_path: Path
) -> None:
    """The configured share action should determine which helper is invoked."""

    target = tmp_path / "photo.jpg"
    settings = StubSettings("copy_file")
    path_provider = StubPathProvider(target)
    controller = controller_factory(
        settings=settings,
        current_path_provider=path_provider,
    )

    copy_file = mocker.patch.object(controller, "_copy_file_to_clipboard")
    copy_path = mocker.patch.object(controller, "_copy_path_to_clipboard")
    reveal = mocker.patch.object(controller, "_reveal_in_file_manager")

    controller._share_button.click()

    copy_file.assert_called_once_with(Path(target))
    copy_path.assert_not_called()
    reveal.assert_not_called()


def test_action_group_updates_preference(controller_factory, qapp: QApplication) -> None:
    """Switching the QAction selection must persist the new preference."""

    settings = StubSettings("reveal_file")
    path_provider = StubPathProvider(Path("/tmp/photo.jpg"))
    controller = controller_factory(
        settings=settings,
        current_path_provider=path_provider,
    )

    controller._share_action_group.triggered.emit(controller._copy_path_action)

    assert settings.get("ui.share_action", "reveal_file") == "copy_path"
