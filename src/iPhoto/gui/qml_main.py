"""QML-based entry point for the iPhoto desktop application.

This module provides a QML/QtQuick based UI as an alternative to the widget-based
implementation in main.py.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType

# Import application context - use absolute imports for consistency
# This module is always run from the package context
from iPhoto.appctx import AppContext
from iPhoto.errors import IPhotoError


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
        self._model: "SidebarModel | None" = None
        self._initialized = False
        
    def initialize(self) -> None:
        """Initialize the sidebar model after app is ready."""
        if self._initialized:
            return
            
        try:
            from iPhoto.gui.ui.models.sidebar_model import SidebarModel
            self._model = SidebarModel(self._context.library, self)
            
            # Connect model signals
            self._model.albumSelected.connect(self._on_album_selected)
            self._model.allPhotosSelected.connect(self.allPhotosSelected)
            self._model.staticNodeSelected.connect(self.staticNodeSelected)
            self._model.bindLibraryRequested.connect(self.bindLibraryRequested)
            
            # Connect library tree updates to hasLibrary changes
            self._context.library.treeUpdated.connect(self.hasLibraryChanged)
            self._initialized = True
        except Exception as e:
            print(f"Failed to initialize sidebar model: {e}")
            traceback.print_exc()
    
    @Property(QObject, constant=False, notify=hasLibraryChanged)
    def model(self) -> "SidebarModel | None":
        """Return the sidebar model for QML binding."""
        return self._model
    
    @Property(bool, constant=False, notify=hasLibraryChanged)
    def hasLibrary(self) -> bool:  # noqa: N802  # Qt property uses camelCase
        """Return whether a library is currently bound."""
        return self._context.library.root() is not None
    
    @Property(str, constant=True)
    def iconDirectory(self) -> str:  # noqa: N802
        """Return the path to the bundled icon directory."""
        icon_dir = Path(__file__).parent / "ui" / "icon"
        return str(icon_dir)
    
    @Slot(str)
    def bindLibrary(self, path: str) -> None:  # noqa: N802  # Qt slot uses camelCase
        """Bind the library to the given path."""
        try:
            self._context.library.bind_path(Path(path))
            if self._model is not None:
                self._model.refresh()
            self.hasLibraryChanged.emit()
        except IPhotoError as e:
            print(f"Failed to bind library: {e}")
    
    @Slot(int)
    def selectItem(self, index: int) -> None:  # noqa: N802  # Qt slot uses camelCase
        """Handle item selection from QML."""
        if self._model is not None:
            self._model.select_item(index)
    
    @Slot(int)
    def toggleExpansion(self, index: int) -> None:  # noqa: N802  # Qt slot uses camelCase
        """Toggle expansion state of an item."""
        if self._model is not None:
            self._model.toggle_expansion(index)
    
    def _on_album_selected(self, path: Path) -> None:
        """Convert Path to string for QML compatibility."""
        self.albumSelected.emit(str(path))


class GalleryBridge(QObject):
    """Bridge between QML and the Python gallery model.
    
    This class exposes the GalleryModel to QML for displaying thumbnails.
    """
    
    countChanged = Signal()  # noqa: N815
    loadingChanged = Signal()  # noqa: N815
    itemSelected = Signal(str)  # noqa: N815
    
    def __init__(self, context: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._model: "GalleryModel | None" = None
        self._initialized = False
        
    def initialize(self) -> None:
        """Initialize the gallery model after app is ready."""
        if self._initialized:
            return
            
        try:
            from iPhoto.gui.ui.qml.gallery_model import GalleryModel
            self._model = GalleryModel(self._context.library, self)
            self._model.countChanged.connect(self.countChanged)
            self._model.loadingChanged.connect(self.loadingChanged)
            self._model.itemSelected.connect(self.itemSelected)
            self._initialized = True
        except Exception as e:
            print(f"Failed to initialize gallery model: {e}")
            traceback.print_exc()
    
    @Property(QObject, constant=False, notify=countChanged)
    def model(self) -> "GalleryModel | None":
        """Return the gallery model for QML binding."""
        return self._model
    
    @Property(int, notify=countChanged)
    def count(self) -> int:
        """Return the number of items in the gallery."""
        if self._model is not None:
            return self._model.count
        return 0
    
    @Property(bool, notify=loadingChanged)
    def loading(self) -> bool:
        """Return whether the gallery is currently loading."""
        if self._model is not None:
            return self._model.loading
        return False
    
    @Slot(str)
    def loadAlbum(self, path: str) -> None:  # noqa: N802
        """Load assets from the given album path."""
        if self._model is not None:
            self._model.loadAlbum(path)
    
    @Slot()
    def loadAllPhotos(self) -> None:  # noqa: N802
        """Load all photos from the library root."""
        if self._model is not None:
            self._model.loadAllPhotos()
    
    @Slot()
    def clear(self) -> None:
        """Clear all items from the gallery."""
        if self._model is not None:
            self._model.clear()
    
    @Slot(int)
    def selectItem(self, index: int) -> None:  # noqa: N802
        """Handle item selection."""
        if self._model is not None:
            self._model.selectItem(index)


class AppBridge(QObject):
    """Bridge exposing high-level actions to QML menus."""

    libraryBound = Signal(str)  # noqa: N815
    albumOpened = Signal(str)  # noqa: N815
    errorRaised = Signal(str)  # noqa: N815

    def __init__(
        self,
        context: AppContext,
        sidebar: SidebarBridge,
        gallery: GalleryBridge,
        thumbnail_provider: QObject | None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._sidebar = sidebar
        self._gallery = gallery
        self._thumbnail_provider = thumbnail_provider

    def _set_library_root_on_provider(self) -> None:
        if self._thumbnail_provider and self._context.library.root():
            try:
                self._thumbnail_provider.set_library_root(self._context.library.root())
            except Exception:
                pass

    @Slot(str)
    def bindLibrary(self, path: str) -> None:  # noqa: N802
        """Bind the basic library and refresh related models."""
        try:
            self._context.library.bind_path(Path(path))
            self._context.settings.set("basic_library_path", str(self._context.library.root()))
            self._sidebar.initialize()
            self._gallery.initialize()
            self._set_library_root_on_provider()
            self.libraryBound.emit(path)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            print(f"Failed to bind library: {msg}")
            self.errorRaised.emit(msg)

    @Slot(str)
    def openAlbum(self, path: str) -> None:  # noqa: N802
        """Open an album folder and populate the gallery view."""
        album_path = Path(path)
        if not album_path.exists() or not album_path.is_dir():
            self.errorRaised.emit(f"Album path not found: {path}")
            return
        try:
            # Remember album for parity with widget workflow
            self._context.remember_album(album_path)
        except Exception:
            pass
        try:
            # Attempt to leverage the backend facade when available
            self._context.facade.open_album(album_path)
        except Exception:
            # Facade may raise if library is not bound; fall back to direct load
            pass
        self._gallery.loadAlbum(str(album_path))
        self.albumOpened.emit(str(album_path))

    @Slot()
    def openAllPhotos(self) -> None:  # noqa: N802
        """Load all photos from the bound library."""
        self._gallery.loadAllPhotos()

    @Slot()
    def rebuildLiveLinks(self) -> None:  # noqa: N802
        """Trigger live link pairing on the current album."""
        try:
            self._context.facade.pair_live_current()
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            print(f"Failed to rebuild live links: {msg}")
            self.errorRaised.emit(msg)

    @Slot()
    def rescanCurrent(self) -> None:  # noqa: N802
        """Start a background rescan for the current album."""
        try:
            self._context.facade.rescan_current_async()
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            print(f"Failed to start rescan: {msg}")
            self.errorRaised.emit(msg)


def main(argv: list[str] | None = None) -> int:
    """Launch the QML application and return the exit code."""
    
    arguments = list(sys.argv if argv is None else argv)
    
    # Set application attributes before creating QGuiApplication
    # This improves startup robustness on various platforms
    try:
        from PySide6.QtCore import Qt
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass  # Attribute may not exist in older Qt versions
    
    app = QGuiApplication(arguments)
    app.setApplicationName("iPhoto")
    app.setOrganizationName("iPhotron")
    
    # Create application context first
    try:
        context = AppContext()
    except Exception as e:
        print(f"Failed to create application context: {e}")
        traceback.print_exc()
        return 1
    
    # Create bridges with lazy initialization
    sidebar_bridge = SidebarBridge(context)
    gallery_bridge = GalleryBridge(context)
    app_bridge: AppBridge | None = None
    
    # Create QML engine
    engine = QQmlApplicationEngine()
    
    # Add image providers for icons and thumbnails
    thumbnail_provider = None
    try:
        from iPhoto.gui.ui.qml.qml_providers import IconImageProvider, ThumbnailImageProvider
        engine.addImageProvider("icons", IconImageProvider())
        thumbnail_provider = ThumbnailImageProvider()
        engine.addImageProvider("thumbnails", thumbnail_provider)
    except Exception as e:
        print(f"Warning: Failed to register image providers: {e}")
        traceback.print_exc()
    
    # Expose bridges to QML BEFORE loading the QML file
    root_context = engine.rootContext()
    root_context.setContextProperty("sidebarBridge", sidebar_bridge)
    root_context.setContextProperty("galleryBridge", gallery_bridge)
    app_bridge = AppBridge(context, sidebar_bridge, gallery_bridge, thumbnail_provider)
    root_context.setContextProperty("appBridge", app_bridge)
    
    # Determine QML file path
    qml_dir = Path(__file__).parent / "ui" / "qml"
    main_qml = qml_dir / "Main.qml"
    
    # Add QML import paths
    engine.addImportPath(str(qml_dir.parent))
    engine.addImportPath(str(qml_dir))
    
    if not main_qml.exists():
        print(f"Error: Main.qml not found at {main_qml}")
        return 1
    
    # Initialize bridges before loading QML
    # This ensures models are ready when QML accesses them
    sidebar_bridge.initialize()
    gallery_bridge.initialize()
    
    # Setup connection to update thumbnail provider with library root
    def update_thumbnail_provider_root():
        if thumbnail_provider and context.library.root():
            thumbnail_provider.set_library_root(context.library.root())

    # Connect signals
    context.library.treeUpdated.connect(update_thumbnail_provider_root)
    # Initial set if library is already bound
    update_thumbnail_provider_root()

    # Load the main QML file
    engine.load(QUrl.fromLocalFile(str(main_qml)))
    
    if not engine.rootObjects():
        print("Error: Failed to load QML root objects")
        for warning in engine.warnings():
            print(f"QML Warning: {warning.toString()}")
        return 1
    
    # Allow opening an album directly via argv[1]
    if len(arguments) > 1:
        # Defer library binding to after event loop starts
        def _bind_library() -> None:
            sidebar_bridge.bindLibrary(arguments[1])
        QTimer.singleShot(100, _bind_library)
    
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
