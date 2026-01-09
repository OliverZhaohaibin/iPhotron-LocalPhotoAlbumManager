from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
import sys

# Provide lightweight PySide6 stubs for headless test collection.
if "PySide6" not in sys.modules:
    sys.modules["PySide6"] = ModuleType("PySide6")
if "PySide6.QtCore" not in sys.modules:
    qtcore = ModuleType("PySide6.QtCore")

    class _Signal:
        def __init__(self, *_, **__):
            self._subs = []

        def connect(self, fn):
            self._subs.append(fn)

        def emit(self, *args, **kwargs):
            for fn in list(self._subs):
                fn(*args, **kwargs)

    class _QModelIndex:
        def __init__(self, row: int = 0, valid: bool = True):
            self._row = row
            self._valid = valid

        def isValid(self):
            return self._valid

        def row(self):
            return self._row

        def column(self):
            return 0

    class _Qt:
        DisplayRole = 0
        DecorationRole = 1
        UserRole = 1000
        EditRole = 2

        class SortOrder:
            DescendingOrder = 1
            AscendingOrder = 0

        class ItemDataRole:
            UserRole = 1000
            DisplayRole = 0
            DecorationRole = 1

    class _QAbstractListModel:
        def __init__(self, *_, **__): ...

        def beginInsertRows(self, *_, **__): ...

        def endInsertRows(self, *_, **__): ...

        def beginRemoveRows(self, *_, **__): ...

        def endRemoveRows(self, *_, **__): ...

        def beginResetModel(self, *_, **__): ...

        def endResetModel(self, *_, **__): ...

        def index(self, row: int, _column: int = 0):
            return _QModelIndex(row=row, valid=True)

    class _QTimer:
        def __init__(self, *_, **__):
            self._active = False
            self.timeout = _Signal()

        def setSingleShot(self, *_):
            return None

        def setInterval(self, *_):
            return None

        def start(self, *_):
            self._active = True

        def stop(self):
            self._active = False

        def isActive(self):
            return self._active

    class _QCoreApplication:
        def __init__(self, *_, **__): ...

        @staticmethod
        def instance():
            return None

    class _QRectF:
        def __init__(self, *_, **__): ...

    class _QSortFilterProxyModel:
        def __init__(self, *_, **__): ...

    class _QRunnable:
        def __init__(self, *_, **__): ...

        def setAutoDelete(self, *_): ...

    class _QThreadPool:
        @staticmethod
        def globalInstance():
            return _QThreadPool()

        def start(self, *_):
            return None

    class _QThread:
        @staticmethod
        def currentThread():
            return None

    class _QFileSystemWatcher:
        def __init__(self, *_, **__): ...

    class _QMutex:
        def __init__(self, *_, **__): ...

    class _QMutexLocker:
        def __init__(self, *_, **__): ...

    class _QByteArray(bytes):
        def __new__(cls, *args, **kwargs):
            return super().__new__(cls, b"")

    class _QSize:
        def __init__(self, *_):
            pass

    class _QAbstractItemModel:
        def __init__(self, *_, **__): ...

    class _QObject:
        def __init__(self, *_, **__): ...

    qtcore.Signal = _Signal
    def _Slot(*args, **kwargs):
        def _decorator(fn):
            return fn
        return _decorator
    qtcore.Slot = _Slot
    qtcore.QObject = _QObject
    qtcore.QModelIndex = _QModelIndex
    qtcore.QAbstractListModel = _QAbstractListModel
    qtcore.QAbstractItemModel = _QAbstractItemModel
    qtcore.QTimer = _QTimer
    qtcore.QRunnable = _QRunnable
    qtcore.QThreadPool = _QThreadPool
    qtcore.QThread = _QThread
    qtcore.QFileSystemWatcher = _QFileSystemWatcher
    qtcore.QMutex = _QMutex
    qtcore.QMutexLocker = _QMutexLocker
    qtcore.QByteArray = _QByteArray
    qtcore.QCoreApplication = _QCoreApplication
    qtcore.QRectF = _QRectF
    qtcore.QSortFilterProxyModel = _QSortFilterProxyModel
    qtcore.QSize = _QSize
    qtcore.Qt = _Qt
    sys.modules["PySide6.QtCore"] = qtcore
    sys.modules["PySide6"].QtCore = qtcore  # type: ignore[attr-defined]
if "PySide6.QtGui" not in sys.modules:
    qtgui = ModuleType("PySide6.QtGui")
    class _QImage:
        pass
    class _QImageReader:
        pass
    class _QPixmap:
        pass
    class _QColor:
        def __init__(self, *_, **__): ...
    class _QIcon:
        pass
    class _QPainter:
        pass
    class _QTransform:
        pass
    class _QPalette:
        pass
    class _QFont:
        def __init__(self, *_, **__): ...

        def setPointSize(self, *_): ...

        def setWeight(self, *_): ...

        class Weight:
            Bold = 75
            Medium = 50
    class _QFontMetrics:
        def __init__(self, *_, **__): ...
    qtgui.QImage = _QImage
    qtgui.QImageReader = _QImageReader
    qtgui.QPixmap = _QPixmap
    qtgui.QColor = _QColor
    qtgui.QIcon = _QIcon
    qtgui.QPainter = _QPainter
    qtgui.QTransform = _QTransform
    qtgui.QPalette = _QPalette
    qtgui.QFont = _QFont
    qtgui.QFontMetrics = _QFontMetrics
    sys.modules["PySide6.QtGui"] = qtgui
    sys.modules["PySide6"].QtGui = qtgui  # type: ignore[attr-defined]

if "PySide6.QtSvg" not in sys.modules:
    qtsvg = ModuleType("PySide6.QtSvg")
    class _QSvgRenderer:
        pass
    qtsvg.QSvgRenderer = _QSvgRenderer
    sys.modules["PySide6.QtSvg"] = qtsvg
    sys.modules["PySide6"].QtSvg = qtsvg  # type: ignore[attr-defined]

if "PySide6.QtWidgets" not in sys.modules:
    qtwidgets = ModuleType("PySide6.QtWidgets")
    class _QWidget:
        pass
    qtwidgets.QWidget = _QWidget
    sys.modules["PySide6.QtWidgets"] = qtwidgets
    sys.modules["PySide6"].QtWidgets = qtwidgets  # type: ignore[attr-defined]

# Bypass heavy package __init__ that pulls Qt dependencies.
if "src.iPhoto.gui" not in sys.modules:
    gui_pkg = ModuleType("src.iPhoto.gui")
    gui_pkg.__path__ = [str(Path(__file__).resolve().parents[3] / "src/iPhoto/gui")]  # type: ignore[attr-defined]
    sys.modules["src.iPhoto.gui"] = gui_pkg

from src.iPhoto.gui.ui.models.asset_list.model import AssetListModel


@pytest.fixture
def model():
    from tests.ui.models.test_asset_list_model import MockFacade, MockAssetCacheManager

    with patch(
        "src.iPhoto.gui.ui.models.asset_list.model.AssetCacheManager",
        side_effect=MockAssetCacheManager,
    ):
        yield AssetListModel(MockFacade())


def test_request_sets_flag(model):
    assert model._optimistic_refresh_requested is False
    model.request_optimistic_refresh()
    assert model._optimistic_refresh_requested is True


def test_try_optimistic_refresh_applies_and_prioritizes(model):
    model._state_manager.set_rows([{"rel": "a.jpg"}])
    model._state_manager.rebuild_lookup()
    model._optimistic_refresh_requested = True
    model._apply_incremental_rows = MagicMock(return_value=True)
    model.prioritize_rows = MagicMock()

    applied = model._try_optimistic_refresh([{"rel": "b.jpg"}], True)

    assert applied is True
    model._apply_incremental_rows.assert_called_once()
    model.prioritize_rows.assert_called_once_with(0, 0)
    assert model._optimistic_refresh_requested is False


def test_try_optimistic_refresh_clears_flag_on_attempt(model):
    model._state_manager.set_rows([{"rel": "a.jpg"}])
    model._state_manager.rebuild_lookup()
    model._optimistic_refresh_requested = True
    model._apply_incremental_rows = MagicMock(return_value=False)

    applied = model._try_optimistic_refresh([{"rel": "b.jpg"}], True)

    assert applied is False
    assert model._optimistic_refresh_requested is False


def test_empty_reset_batch_clears_flag(model):
    model.request_optimistic_refresh()
    model._on_batch_ready([], True)
    assert model._optimistic_refresh_requested is False


def test_load_completion_clears_flag(model):
    model.request_optimistic_refresh()
    model._on_controller_load_finished(Path("/tmp"), True)
    assert model._optimistic_refresh_requested is False


def test_no_existing_rows_disables_optimistic_merge(model):
    model._optimistic_refresh_requested = True
    model._apply_incremental_rows = MagicMock(return_value=True)

    applied = model._try_optimistic_refresh([{"rel": "b.jpg"}], True)

    assert applied is False
    model._apply_incremental_rows.assert_not_called()
    assert model._optimistic_refresh_requested is True
