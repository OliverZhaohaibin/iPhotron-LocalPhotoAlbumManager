"""Tests for the demo-aligned video trim bar widget."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)

from PySide6.QtCore import QPoint, QPointF
from PySide6.QtWidgets import QApplication, QFrame, QPushButton

from iPhoto.gui.ui.widgets.video_trim_bar import (
    BAR_HEIGHT,
    BOTTOM_BG_COLOR,
    HOVER_COLOR,
    THEME_COLOR,
    TRIM_HIGHLIGHT_COLOR,
    VideoTrimBar,
    _HandleButton,
)


@pytest.fixture(scope="module")
def qapp():
    """Provide a QApplication instance for widget tests."""

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_video_trim_bar_matches_demo_palette() -> None:
    """The production trim bar should keep the demo's gray/yellow palette."""

    assert THEME_COLOR == "#3a3a3a"
    assert HOVER_COLOR == "#505050"
    assert TRIM_HIGHLIGHT_COLOR == "#FFD60A"
    assert BOTTOM_BG_COLOR == "#252525"


def test_video_trim_bar_builds_demo_transport_shell(qapp) -> None:
    """The trim bar should expose the demo-style bottom frame and play button."""

    bar = VideoTrimBar()

    bottom_frame = bar.findChild(QFrame, "BottomControlFrame")
    play_button = bar.findChild(QPushButton, "PlayButton")

    assert bar.height() == BAR_HEIGHT + 30
    assert bottom_frame is not None
    assert play_button is bar._play_button
    assert play_button.width() == 50
    assert play_button.height() == BAR_HEIGHT
    assert bar._strip_host.height() == BAR_HEIGHT


def test_play_button_emits_transport_signal(qapp) -> None:
    """Clicking the left transport button should emit playPauseRequested."""

    bar = VideoTrimBar()
    calls: list[bool] = []
    bar.playPauseRequested.connect(lambda: calls.append(True))

    bar._play_button.click()

    assert calls == [True]


def test_set_playing_tracks_transport_state(qapp) -> None:
    """set_playing should update the trim bar's cached transport state."""

    bar = VideoTrimBar()

    bar.set_playing(True)
    assert bar.is_playing() is True

    bar.set_playing(False)
    assert bar.is_playing() is False


def test_handle_drag_uses_parent_global_position_not_local_clip() -> None:
    """Dragging should follow the global cursor position even past button edges."""

    fake_parent = SimpleNamespace(mapFromGlobal=lambda point: QPoint(180, point.y()))
    fake_event = SimpleNamespace(
        globalPosition=lambda: QPointF(420.0, 32.0),
        position=lambda: QPointF(23.0, 12.0),
    )

    parent_x = _HandleButton._parent_x_from_event(fake_parent, fake_event)

    assert parent_x == 180
