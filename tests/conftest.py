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

# Mock QtMultimedia to avoid libpulse dependency in headless tests
if "PySide6.QtMultimedia" not in sys.modules:
    mock_mm = MagicMock()
    mock_mm.__spec__ = MagicMock()
    sys.modules["PySide6.QtMultimedia"] = mock_mm

if "PySide6.QtMultimediaWidgets" not in sys.modules:
    mock_mmw = MagicMock()
    mock_mmw.__spec__ = MagicMock()
    sys.modules["PySide6.QtMultimediaWidgets"] = mock_mmw

# Mock OpenGL to avoid display dependency
if "OpenGL" not in sys.modules:
    mock_gl = MagicMock()
    mock_gl.__spec__ = MagicMock()
    sys.modules["OpenGL"] = mock_gl
    sys.modules["OpenGL.GL"] = MagicMock()
