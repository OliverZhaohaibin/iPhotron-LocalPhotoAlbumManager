import sys
import os
from types import ModuleType
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Disable auto-loading third-party pytest plugins that may pull native Qt backends.
os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")

# Ensure the project sources are importable as ``src`` to match legacy tests.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "iPhotos" not in sys.modules:
    pkg = ModuleType("iPhotos")
    pkg.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    sys.modules["iPhotos"] = pkg

HAS_PYSIDE6 = True
HAS_QTWIDGETS = False
try:
    import PySide6  # type: ignore  # noqa: F401
except ImportError:
    HAS_PYSIDE6 = False

# Helper to conditionally mock modules
def ensure_module(name: str, mock_obj: object = None) -> None:
    try:
        __import__(name)
    except ImportError:
        if name not in sys.modules:
            if mock_obj is None:
                mock_obj = MagicMock()
                mock_obj.__spec__ = MagicMock()
            sys.modules[name] = mock_obj
            # Attach as attribute to parent (e.g., PySide6.QtWidgets)
            parent_name, _, attr = name.rpartition(".")
            if parent_name and parent_name in sys.modules:
                setattr(sys.modules[parent_name], attr, mock_obj)

if HAS_PYSIDE6:
    # Attempt to import QtWidgets; if unavailable, allow tests guarded with importorskip to skip.
    try:
        from PySide6 import QtWidgets  # type: ignore
        QWidget = QtWidgets.QWidget
        QApplication = QtWidgets.QApplication
        HAS_QTWIDGETS = True
    except ImportError:
        QWidget = MagicMock()
        QApplication = None

    # Provide minimal QApplication/QCoreApplication shims when PySide6 QtWidgets is unavailable
    if not HAS_QTWIDGETS:
        class _MockQApplication:
            _instance = None

            def __init__(self, *args, **kwargs):
                type(self)._instance = self

            @classmethod
            def instance(cls):
                return cls._instance

            def processEvents(self):
                return None

        QApplication = _MockQApplication  # type: ignore

    # Force-mock QtOpenGLWidgets and QtOpenGL to avoid segmentation faults in headless environment.
    if HAS_QTWIDGETS:
        class MockQOpenGLWidget(QWidget):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

            def makeCurrent(self): pass
            def doneCurrent(self): pass
            def context(self): return MagicMock()

        mock_gl_widgets = MagicMock()
        mock_gl_widgets.QOpenGLWidget = MockQOpenGLWidget
        sys.modules["PySide6.QtOpenGLWidgets"] = mock_gl_widgets

        sys.modules["PySide6.QtOpenGL"] = MagicMock()

    # PySide6.QtGui needs special handling for mocks if it is missing
    try:
        import PySide6.QtGui
    except ImportError:
        if "PySide6.QtGui" not in sys.modules:
            mock_gui = MagicMock()
            mock_gui.__spec__ = MagicMock()

            # Define dummy classes for types used in type hints or Slots
            class MockQtClass:
                def __init__(self, *args, **kwargs): pass
                def __getattr__(self, name): return MagicMock()

            class MockQImage(MockQtClass):
                def isNull(self) -> bool: return False
                def width(self) -> int: return 0
                def height(self) -> int: return 0
                def copy(self, *_args, **_kwargs) -> "MockQImage": return self
                def save(self, *_args, **_kwargs) -> bool: return True
            class MockQColor(MockQtClass): pass
            class MockQPixmap(MockQtClass): pass
            class MockQIcon(MockQtClass): pass
            class MockQPainter(MockQtClass): pass
            class MockQPen(MockQtClass): pass
            class MockQBrush(MockQtClass): pass
            class MockQMouseEvent(MockQtClass): pass
            class MockQResizeEvent(MockQtClass): pass
            class MockQPaintEvent(MockQtClass): pass
            class MockQPalette(MockQtClass):
                class ColorRole:
                    Window = 1
                    WindowText = 2
                    Base = 3
                    AlternateBase = 4
                    ToolTipBase = 5
                    ToolTipText = 6
                    Text = 7
                    Button = 8
                    ButtonText = 9
                    BrightText = 10
                    Link = 11
                    Highlight = 12
                    HighlightedText = 13
                    Mid = 14
                    Midlight = 15
                    Shadow = 16
                    Dark = 17

            mock_gui.QImage = MockQImage
            mock_gui.QColor = MockQColor
            mock_gui.QPixmap = MockQPixmap
            mock_gui.QIcon = MockQIcon
            mock_gui.QPainter = MockQPainter
            mock_gui.QPen = MockQPen
            mock_gui.QBrush = MockQBrush
            mock_gui.QMouseEvent = MockQMouseEvent
            mock_gui.QResizeEvent = MockQResizeEvent
            mock_gui.QPaintEvent = MockQPaintEvent
            mock_gui.QPalette = MockQPalette

            sys.modules["PySide6.QtGui"] = mock_gui
            if "PySide6" in sys.modules:
                setattr(sys.modules["PySide6"], "QtGui", mock_gui)

    ensure_module("PySide6.QtSvg")

    # Provide QtTest shim if unavailable
    try:
        import PySide6.QtTest  # type: ignore  # noqa: F401
    except ImportError:
        qt_test_module = ModuleType("PySide6.QtTest")

        class QSignalSpy(list):
            def __init__(self, signal):
                super().__init__()
                self._signal = signal
                if hasattr(signal, "connect"):
                    signal.connect(self._capture)

            def _capture(self, *args):
                self.append(args)

            def count(self):
                return len(self)

        class QTest:
            @staticmethod
            def qWait(_ms: int) -> None:
                return None

        qt_test_module.QSignalSpy = QSignalSpy
        qt_test_module.QTest = QTest
        sys.modules["PySide6.QtTest"] = qt_test_module
        if "PySide6" in sys.modules:
            setattr(sys.modules["PySide6"], "QtTest", qt_test_module)
    else:
        ensure_module("PySide6.QtTest")

    # Mock OpenGL to avoid display dependency
    ensure_module("OpenGL")
if "OpenGL" in sys.modules and isinstance(sys.modules["OpenGL"], MagicMock):
    sys.modules["OpenGL.GL"] = MagicMock()


def pytest_collection_modifyitems(config, items):
    """Skip Qt-heavy tests when QtWidgets backend is unavailable."""
    if HAS_QTWIDGETS:
        return
    import pytest

    skip_qt = pytest.mark.skip(reason="QtWidgets backend unavailable (missing libEGL)")
    for item in items:
        nodeid = item.nodeid.lower()
        if "ui/" in nodeid or "gui" in nodeid or "qwidget" in nodeid or "qtwidgets" in nodeid:
            item.add_marker(skip_qt)


def pytest_ignore_collect(collection_path, config):
    """Avoid importing Qt-dependent test modules when QtWidgets is missing."""
    if HAS_QTWIDGETS:
        return False
    path_str = str(collection_path)
    if "/ui/" in path_str or "/gui/" in path_str:
        return True
    return False
