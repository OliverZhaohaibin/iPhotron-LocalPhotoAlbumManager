"""Tests for AlbumsDashboard and AlbumCard widgets."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 is required for UI tests",
    exc_type=ImportError,
)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from iPhotos.src.iPhoto.gui.ui.widgets.albums_dashboard import AlbumCard, AlbumsDashboard

@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

@pytest.fixture
def mock_library():
    lib = MagicMock()
    lib.list_albums.return_value = []
    # Mock treeUpdated signal
    lib.treeUpdated = MagicMock()
    lib.treeUpdated.connect = MagicMock()
    return lib

def test_album_card_initialization(qapp):
    """Test that AlbumCard initializes correctly with path."""
    path = Path("/tmp/test_album")
    card = AlbumCard(path, "My Album", 10)

    assert card.path == path
    assert card.title_label.text() == "My Album"
    assert card.count_label.text() == "10"
    # Verify cursor
    assert card.cursor().shape() == Qt.CursorShape.PointingHandCursor
    # Verify mouse tracking
    assert card.hasMouseTracking()

def test_album_card_clicked_signal(qtbot):
    """Test that AlbumCard emits clicked signal with path."""
    path = Path("/tmp/test_album")
    card = AlbumCard(path, "My Album", 10)
    qtbot.addWidget(card)

    with qtbot.waitSignal(card.clicked) as blocker:
        qtbot.mouseClick(card, Qt.MouseButton.LeftButton)

    assert blocker.args == [path]

def test_album_card_hover_effect(qtbot):
    """Test that AlbumCard handles mouse events for hover effect."""
    path = Path("/tmp/test_album")
    card = AlbumCard(path, "My Album", 10)
    qtbot.addWidget(card)
    card.show()

    # We can't easily verify the painting output without a screenshot test,
    # but we can verify the state changes in mouseMoveEvent.
    # We'll rely on the fact that paintEvent is called without error.

    # Move mouse over card
    qtbot.mouseMove(card, card.rect().center())

    # Just ensure no crash in paintEvent
    card.repaint()

def test_albums_dashboard_populates_cards(qtbot, mock_library):
    """Test that dashboard creates cards from library albums."""
    album1 = MagicMock()
    album1.title = "Album 1"
    album1.path = Path("/path/to/album1")

    album2 = MagicMock()
    album2.title = "Album 2"
    album2.path = Path("/path/to/album2")

    mock_library.list_albums.return_value = [album1, album2]

    # Prevent thread pool from running workers
    with patch("PySide6.QtCore.QThreadPool.globalInstance") as mock_pool:
        dashboard = AlbumsDashboard(mock_library)
        qtbot.addWidget(dashboard)

        assert len(dashboard._cards) == 2
        assert album1.path in dashboard._cards
        assert album2.path in dashboard._cards

        # Verify card 1 has correct path
        assert dashboard._cards[album1.path].path == album1.path

def test_albums_dashboard_relays_signal(qtbot, mock_library):
    """Test that dashboard relays the clicked signal from card."""
    album = MagicMock()
    album.title = "Test Album"
    album.path = Path("/path/to/album")
    mock_library.list_albums.return_value = [album]

    with patch("PySide6.QtCore.QThreadPool.globalInstance"):
        dashboard = AlbumsDashboard(mock_library)
        qtbot.addWidget(dashboard)
        card = dashboard._cards[album.path]

        with qtbot.waitSignal(dashboard.albumSelected) as blocker:
            # Simulate click on the card
            # We can emit the card's signal directly to test relay
            card.clicked.emit(album.path)

        assert blocker.args == [album.path]
