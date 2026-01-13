"""QML-based entry point for the iPhoto desktop application.

This module provides a QML/QtQuick based UI as an alternative to the widget-based
implementation in main.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType

from ..appctx import AppContext
from .ui.models.gallery_model import GalleryModel
from .ui.models.sidebar_model import SidebarModel


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
    app = QGuiApplication(arguments)
    
    # Register custom types with QML
    qmlRegisterType(SidebarModel, "iPhoto", 1, 0, "SidebarModel")
    qmlRegisterType(GalleryModel, "iPhoto", 1, 0, "GalleryModel")
    
    engine = QQmlApplicationEngine()
    
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
    
    if not main_qml.exists():
        print(f"Error: Main.qml not found at {main_qml}")
        return 1
    
    engine.load(QUrl.fromLocalFile(str(main_qml)))
    
    if not engine.rootObjects():
        print("Error: Failed to load QML root objects")
        return 1
    
    # Allow opening an album directly via argv[1]
    if len(arguments) > 1:
        sidebar_bridge.bindLibrary(arguments[1])
    
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
