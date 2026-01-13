"""QML-based entry point for the iPhoto desktop application.

This module provides a QML/QtQuick based UI as an alternative to the widget-based
implementation in main.py.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path
import os

from typing import Callable

from PySide6.QtCore import (
    Property,
    QCoreApplication,
    QObject,
    QMessageLogContext,
    Qt,
    QtMsgType,
    QUrl,
    Signal,
    Slot,
    qInstallMessageHandler,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlError, qmlRegisterType
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from ..appctx import AppContext
from .ui.models.gallery_model import GalleryModel
from .ui.models.sidebar_model import SidebarModel

# Icon directory path
ICON_DIR = Path(__file__).parent / "ui" / "icon"


def _qt_message_handler(mode: QtMsgType, context: QMessageLogContext | None, message: str) -> None:
    """Route Qt/QML messages to the Python console for easier debugging."""
    level_map = {
        QtMsgType.QtDebugMsg: "DEBUG",
        QtMsgType.QtInfoMsg: "INFO",
        QtMsgType.QtWarningMsg: "WARNING",
        QtMsgType.QtCriticalMsg: "CRITICAL",
        QtMsgType.QtFatalMsg: "FATAL",
    }
    level = level_map.get(mode, f"LEVEL_{int(mode)}")
    location = ""
    if context and context.file:
        location = f" ({context.file}:{context.line})"
    print(f"[Qt/{level}] {message}{location}")


def _install_qt_logger() -> None:
    """Install a Qt message handler to forward QML/Qt errors to stdout."""
    try:
        qInstallMessageHandler(_qt_message_handler)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[Qt] Failed to install message handler: {exc}")


def _enable_windows_software_opengl() -> None:
    """Enable software OpenGL fallback on Windows before app creation."""
    if platform.system().lower() != "windows":
        return
    try:
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL)
        print("[Qt] Enabled software OpenGL fallback for Windows.")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[Qt] Unable to configure Qt Quick software rendering: {exc}")


def _set_windows_quick_software_backend() -> None:
    """Select the Qt Quick software renderer on Windows.
    
    Call this after creating the Qt application but before any QQuickWindow
    instances are constructed.
    """
    if platform.system().lower() != "windows":
        return
    try:
        QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)
        print("[Qt] Using software renderer for Qt Quick.")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[Qt] Unable to configure Qt Quick software rendering: {exc}")


def _apply_windows_env_fallbacks() -> None:
    """Set environment-based fallbacks for Windows Qt rendering."""
    if platform.system().lower() != "windows":
        return
    env_overrides = {
        "QSG_RHI_BACKEND": "software",  # Force Qt Quick to software scene graph
        "QT_OPENGL": "software",  # Prefer software OpenGL if still used
    }
    for key, value in env_overrides.items():
        if os.environ.get(key, "").lower() != value:
            os.environ[key] = value
            print(f"[Qt] Set {key}={value} for Windows fallback.")


def _make_qml_warning_logger() -> tuple[Callable[[list[QQmlError]], None], list[str]]:
    """Return a QML warning handler and captured storage."""
    storage: list[str] = []
    seen: set[str] = set()

    def _log_qml_warnings(warnings: list[QQmlError]) -> None:
        for warning in warnings:
            try:
                text = warning.toString()
            except AttributeError:  # pragma: no cover - defensive
                text = str(warning)
            if text in seen:
                continue
            seen.add(text)
            storage.append(text)
            print(f"[QML warning] {text}")

    return _log_qml_warnings, storage


class SidebarBridge(QObject):
    """Bridge between QML and the Python sidebar model.
    
    This class exposes the SidebarModel to QML and handles signals
    for album selection and library binding.
    """
    
    # Qt Signals use camelCase by convention (noqa: N815)
    albumSelected = Signal(str)  # noqa: N815  # Emits the album path as string
    allPhotosSelected = Signal()  # noqa: N815
    staticNodeSelected = Signal(str)  # noqa: N815  # Emits the static node title
    bindLibraryRequested = Signal()  # noqa: N815
    hasLibraryChanged = Signal()  # noqa: N815  # Notify when library binding changes
    
    def __init__(self, context: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._model = SidebarModel(context.library, self)
        self._gallery_model = GalleryModel(context.library, self)
        
        # Connect model signals
        self._model.albumSelected.connect(self._on_album_selected)
        self._model.allPhotosSelected.connect(self._on_all_photos_selected)
        self._model.staticNodeSelected.connect(self.staticNodeSelected)
        self._model.bindLibraryRequested.connect(self.bindLibraryRequested)
        
        # Connect library tree updates to hasLibrary changes
        self._context.library.treeUpdated.connect(self.hasLibraryChanged)
    
    @Property(QObject, constant=True)
    def model(self) -> SidebarModel:
        """Return the sidebar model for QML binding."""
        return self._model
    
    @Property(QObject, constant=True)
    def galleryModel(self) -> GalleryModel:  # noqa: N802
        """Return the gallery model for QML binding."""
        return self._gallery_model
    
    @Property(str, constant=True)
    def iconDir(self) -> str:  # noqa: N802
        """Return the path to the icon directory for QML."""
        return str(ICON_DIR)
    
    @Property(bool, constant=False, notify=hasLibraryChanged)
    def hasLibrary(self) -> bool:  # noqa: N802  # Qt property uses camelCase
        """Return whether a library is currently bound."""
        return self._context.library.root() is not None
    
    @Slot(str)
    def bindLibrary(self, path: str) -> None:  # noqa: N802  # Qt slot uses camelCase
        """Bind the library to the given path."""
        from ..errors import IPhotoError
        try:
            self._context.library.bind_path(Path(path))
            self._model.refresh()
        except IPhotoError as e:
            print(f"Failed to bind library: {e}")
    
    @Slot(int)
    def selectItem(self, index: int) -> None:  # noqa: N802  # Qt slot uses camelCase
        """Handle item selection from QML."""
        self._model.select_item(index)
    
    @Slot(int)
    def toggleExpansion(self, index: int) -> None:  # noqa: N802  # Qt slot uses camelCase
        """Toggle expansion state of an item."""
        self._model.toggle_expansion(index)
    
    def _on_album_selected(self, path: Path) -> None:
        """Handle album selection - load gallery and emit signal."""
        self._gallery_model.loadAlbum(str(path))
        self.albumSelected.emit(str(path))
    
    def _on_all_photos_selected(self) -> None:
        """Handle All Photos selection."""
        self._gallery_model.loadAllPhotos()
        self.allPhotosSelected.emit()


def main(argv: list[str] | None = None) -> int:
    """Launch the QML application and return the exit code."""
    
    arguments = list(sys.argv if argv is None else argv)
    # Install logger early to capture startup/plugin diagnostics.
    _install_qt_logger()
    _apply_windows_env_fallbacks()
    _enable_windows_software_opengl()
    
    print(f"[qml_main] Starting QML engine with arguments: {arguments}")
    app = QGuiApplication(arguments)
    _set_windows_quick_software_backend()
    
    # Register custom types with QML
    qmlRegisterType(SidebarModel, "iPhoto", 1, 0, "SidebarModel")
    qmlRegisterType(GalleryModel, "iPhoto", 1, 0, "GalleryModel")
    
    qml_warning_handler, captured_warnings = _make_qml_warning_logger()
    engine = QQmlApplicationEngine()

    def _on_object_created(obj: QObject | None, url: QUrl) -> None:
        if obj is None:
            print(f"[qml_main] Failed to create root object from {url.toString()}")

    engine.warnings.connect(qml_warning_handler)
    engine.objectCreated.connect(_on_object_created)
    
    # Create application context and sidebar bridge
    context = AppContext()
    sidebar_bridge = SidebarBridge(context)
    
    # Expose the bridge to QML
    engine.rootContext().setContextProperty("sidebarBridge", sidebar_bridge)
    
    # Load the main QML file
    qml_dir = Path(__file__).parent / "ui" / "qml"
    main_qml = qml_dir / "Main.qml"
    
    # Add QML import path so components can be found
    engine.addImportPath(str(qml_dir.parent))
    engine.addImportPath(str(qml_dir))
    print(f"[qml_main] QML import paths: {engine.importPathList()}")
    
    if not main_qml.exists():
        print(f"Error: Main.qml not found at {main_qml}")
        return 1
    
    print(f"[qml_main] Loading QML from: {main_qml}")
    engine.load(QUrl.fromLocalFile(str(main_qml)))
    
    if not engine.rootObjects():
        print("Error: Failed to load QML root objects")
        if captured_warnings:
            print(
                f"[qml_main] {len(captured_warnings)} QML warning(s) were captured during load."
            )
        return 1
    
    # Allow opening an album directly via argv[1]
    if len(arguments) > 1:
        sidebar_bridge.bindLibrary(arguments[1])
    
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
