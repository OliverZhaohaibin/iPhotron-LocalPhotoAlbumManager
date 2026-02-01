from PySide6.QtCore import QItemSelectionModel, QPersistentModelIndex, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication
import pytest

from src.iPhoto.gui.coordinators.playback_coordinator import (
    FILMSTRIP_RECHECK_DELAY_MS,
    FILMSTRIP_SYNC_MAX_RETRIES,
    FILMSTRIP_SYNC_RETRY_DELAY_MS,
    PlaybackCoordinator,
)
from src.iPhoto.gui.ui.widgets.filmstrip_view import FilmstripView


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _identity_row(row: int) -> int:
    return row


def _build_filmstrip() -> tuple[FilmstripView, QStandardItemModel]:
    view = FilmstripView()
    model = QStandardItemModel()
    for _ in range(3):
        model.appendRow(QStandardItem("item"))
    view.setModel(model)
    return view, model


def test_pending_center_resolution_priority(qapp):
    view, model = _build_filmstrip()
    selection_index = model.index(2, 0)
    view.selectionModel().setCurrentIndex(
        selection_index, QItemSelectionModel.ClearAndSelect
    )

    view._pending_center_index = QPersistentModelIndex(model.index(0, 0))
    view._last_center_index = QPersistentModelIndex(model.index(1, 0))
    assert view._resolve_pending_center_index().row() == 0

    view._pending_center_index = QPersistentModelIndex()
    assert view._resolve_pending_center_index().row() == 1

    view._last_center_index = QPersistentModelIndex()
    assert view._resolve_pending_center_index().row() == 2


def _make_playback() -> PlaybackCoordinator:
    playback = PlaybackCoordinator.__new__(PlaybackCoordinator)
    super(PlaybackCoordinator, playback).__init__()
    playback._current_row = 0
    playback._filmstrip_scroll_sync_pending = True
    playback._filmstrip_sync_attempts = 0
    return playback


def test_filmstrip_sync_retries(monkeypatch, qapp):
    playback = _make_playback()
    playback._resolve_valid_row = _identity_row
    scheduled = []

    def _fake_single_shot(delay, callback):
        scheduled.append((delay, callback))

    monkeypatch.setattr(QTimer, "singleShot", staticmethod(_fake_single_shot))
    playback._sync_filmstrip_selection = lambda row: False

    playback._apply_filmstrip_sync()

    assert playback._filmstrip_sync_attempts == 1
    assert playback._filmstrip_scroll_sync_pending is True
    assert scheduled[0][0] == FILMSTRIP_SYNC_RETRY_DELAY_MS


def test_filmstrip_sync_retry_limit(monkeypatch, qapp):
    playback = _make_playback()
    playback._resolve_valid_row = _identity_row
    playback._filmstrip_sync_attempts = FILMSTRIP_SYNC_MAX_RETRIES
    scheduled = []

    def _fake_single_shot(delay, callback):
        scheduled.append((delay, callback))

    monkeypatch.setattr(QTimer, "singleShot", staticmethod(_fake_single_shot))
    playback._sync_filmstrip_selection = lambda row: False

    playback._apply_filmstrip_sync()

    assert playback._filmstrip_scroll_sync_pending is False
    assert playback._filmstrip_sync_attempts == 0
    assert scheduled == []


def test_filmstrip_recheck_scheduled(monkeypatch, qapp):
    playback = _make_playback()
    playback._filmstrip_recheck_pending = False
    playback._current_row = 5
    scheduled = []

    def _fake_single_shot(delay, callback):
        scheduled.append((delay, callback))

    monkeypatch.setattr(QTimer, "singleShot", staticmethod(_fake_single_shot))

    playback._schedule_filmstrip_recheck()

    assert playback._filmstrip_recheck_pending is True
    assert scheduled[0][0] == FILMSTRIP_RECHECK_DELAY_MS


def test_filmstrip_recheck_applies(monkeypatch, qapp):
    playback = _make_playback()
    playback._current_row = 4
    playback._filmstrip_recheck_pending = True
    playback._resolve_valid_row = _identity_row
    calls = []

    def _fake_sync(row):
        calls.append(row)
        return True

    playback._sync_filmstrip_selection = _fake_sync

    playback._apply_filmstrip_recheck()

    assert playback._filmstrip_recheck_pending is False
    assert calls == [4]


def test_filmstrip_recheck_ignored_when_no_row(monkeypatch, qapp):
    playback = _make_playback()
    playback._current_row = -1
    playback._filmstrip_recheck_pending = True
    playback._sync_filmstrip_selection = lambda row: True
    playback._resolve_valid_row = lambda row: -1

    playback._apply_filmstrip_recheck()

    assert playback._filmstrip_recheck_pending is False


def test_resolve_valid_row_from_selection(monkeypatch, qapp):
    playback = _make_playback()

    class MockIndex:
        def __init__(self, valid=True, row_value=0):
            self._valid = valid
            self._row_value = row_value

        def isValid(self):
            return self._valid

        def row(self):
            return self._row_value

    class MockSelectionModel:
        def currentIndex(self):
            return MockIndex(valid=True, row_value=0)

    class MockProxyModel:
        def mapToSource(self, proxy_index):
            return MockIndex(valid=True, row_value=2)

    class MockFilmstripView:
        def selectionModel(self):
            return MockSelectionModel()

        def model(self):
            return MockProxyModel()

    class MockAssetViewModel:
        def rowCount(self):
            return 5

    playback._filmstrip_view = MockFilmstripView()
    playback._asset_vm = MockAssetViewModel()

    assert playback._resolve_valid_row(-1) == 2


def test_resolve_valid_row_keeps_valid(monkeypatch, qapp):
    playback = _make_playback()

    class MockAssetViewModel:
        def rowCount(self):
            return 3

    playback._asset_vm = MockAssetViewModel()

    assert playback._resolve_valid_row(1) == 1


def test_resolve_valid_row_invalid_selection(monkeypatch, qapp):
    playback = _make_playback()

    class MockIndex:
        def __init__(self, valid):
            self._valid = valid

        def isValid(self):
            return self._valid

    class MockSelectionModel:
        def currentIndex(self):
            return MockIndex(valid=False)

    class MockFilmstripView:
        def selectionModel(self):
            return MockSelectionModel()

        def model(self):
            return None

    class MockAssetViewModel:
        def rowCount(self):
            return 0

    playback._filmstrip_view = MockFilmstripView()
    playback._asset_vm = MockAssetViewModel()

    assert playback._resolve_valid_row(-1) == -1
