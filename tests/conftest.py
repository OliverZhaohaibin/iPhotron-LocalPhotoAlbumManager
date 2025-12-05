import sys
from types import ModuleType
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Ensure the project sources are importable as ``iPhotos.src`` to match legacy tests.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "iPhotos" not in sys.modules:
    pkg = ModuleType("iPhotos")
    pkg.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    sys.modules["iPhotos"] = pkg

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

# Mock QtMultimedia to avoid libpulse dependency in headless tests
ensure_module("PySide6.QtMultimedia")
ensure_module("PySide6.QtMultimediaWidgets")

# Core Qt modules - try to import, mock if missing
ensure_module("PySide6.QtWidgets")

# Import QWidget safely to use as a base class for MockQOpenGLWidget
try:
    from PySide6.QtWidgets import QWidget
except ImportError:
    # If imports fail despite ensure_module (unlikely unless mocked), fallback to MagicMock
    QWidget = MagicMock()

# Force-mock QtOpenGLWidgets and QtOpenGL to avoid segmentation faults in headless environment.
# Even if these modules are importable, using them (e.g. creating QOpenGLWidget subclasses)
# can cause crashes without a proper display server.
# Instead of a raw MagicMock, we provide a safe QWidget-based mock for QOpenGLWidget
# so that subclasses (like GLImageViewer) remain valid QObjects and don't crash Signals.
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

        class MockQImage(MockQtClass): pass
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

ensure_module("PySide6.QtSvg")
ensure_module("PySide6.QtTest")

# Mock OpenGL to avoid display dependency
ensure_module("OpenGL")
if "OpenGL" in sys.modules and isinstance(sys.modules["OpenGL"], MagicMock):
    sys.modules["OpenGL.GL"] = MagicMock()
