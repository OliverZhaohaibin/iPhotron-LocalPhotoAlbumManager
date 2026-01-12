"""QML-based GUI entry point for the iPhoto desktop application.

This module provides an alternative entry point that uses a pure QML
interface instead of the mixed QWidget/QML approach. It initializes
the QML engine, registers Python types, and loads the main QML file.

Usage:
    python -m iPhoto.gui.qml_main
    # or
    iphoto-gui-qml (if added to pyproject.toml scripts)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PySide6.QtGui import QGuiApplication

# Ensure the src directory is in the Python path when running directly
_SRC_DIR = Path(__file__).resolve().parent.parent.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QFileDialog

if TYPE_CHECKING:
    from iPhoto.appctx import AppContext

# Path to the QML directory
QML_DIR = Path(__file__).parent / "qml"


class _NavigationController(QObject):
    """Bridge album navigation requests from QML to the facade."""

    def __init__(
        self,
        *,
        context: "AppContext",
        album_controller,
        on_model_changed: Callable[[object], None],
    ) -> None:
        super().__init__()
        self._context = context
        self._album_controller = album_controller
        self._on_model_changed = on_model_changed

    @Slot(str)
    def openAlbum(self, path: str) -> None:
        album_path = Path(path).expanduser()
        if not album_path.exists():
            return
        album = self._context.facade.open_album(album_path)
        if album is None:
            return
        self._context.remember_album(album_path)
        self._album_controller.selectAlbum(str(album.root))
        self._on_model_changed(self._context.facade.asset_list_model)

    @Slot()
    def openAllPhotos(self) -> None:
        library_root = self._context.library.root()
        if library_root is None:
            return
        self._context.facade.open_album(library_root)
        # Ensure any leftover filter from static views is cleared
        self._context.facade.asset_list_model.set_filter_mode(None)
        self._album_controller.selectAllPhotos()
        self._on_model_changed(self._context.facade.asset_list_model)

    @Slot(str)
    def openStaticNode(self, title: str) -> None:
        """Handle static collections like Videos/Favorites."""

        library_root = self._context.library.root()
        if library_root is None:
            return

        normalized = title.casefold()
        filter_mode = None
        if normalized == "videos":
            filter_mode = "video"
        elif normalized == "live photos":
            filter_mode = "live"
        elif normalized == "favorites":
            filter_mode = "favorite"

        if filter_mode is not None:
            switched = self._context.facade.switch_to_library_model_for_static_collection(
                library_root, title, filter_mode
            )
            if not switched:
                self._context.facade.open_album(library_root)
                self._context.facade.asset_list_model.set_filter_mode(filter_mode)
            self._on_model_changed(self._context.facade.asset_list_model)
        else:
            self.openAllPhotos()

    @Slot(str)
    def bindLibrary(self, path: str) -> None:
        target = Path(path).expanduser()
        if not target.exists():
            return
        try:
            self._context.library.bind_path(target)
        except Exception as exc:  # pragma: no cover - dialog feedback only
            print(f"Failed to bind library at {target}: {exc}", file=sys.stderr)
            return
        self._context.settings.set("basic_library_path", str(target))
        self.openAlbum(str(target))

    @Slot()
    def rescanCurrent(self) -> None:
        """Trigger a background rescan for the active album."""

        self._context.facade.rescan_current_async()

    def open_default_album(self) -> None:
        """Open the most relevant album (recent or bound library) on startup."""

        for candidate in self._context.recent_albums:
            if candidate.exists():
                self.openAlbum(str(candidate))
                return

        library_root = self._context.library.root()
        if library_root is not None:
            self.openAlbum(str(library_root))


class _DialogController(QObject):
    """Exposes native dialogs to QML for common actions."""

    def __init__(self, navigation: _NavigationController) -> None:
        super().__init__()
        self._navigation = navigation

    @Slot()
    def openAlbumDialog(self) -> None:
        path = QFileDialog.getExistingDirectory(None, "Open Album Folder")
        if path:
            self._navigation.openAlbum(path)

    @Slot()
    def bindLibraryDialog(self) -> None:
        path = QFileDialog.getExistingDirectory(None, "Select Basic Library")
        if path:
            self._navigation.bindLibrary(path)


class QMLApplication:
    """QML-based application manager.

    This class encapsulates the QML engine initialization, controller
    registration, and main window loading. It provides a cleaner separation
    between Python backend logic and QML frontend presentation.

    Attributes:
        engine: The QQmlApplicationEngine instance
        context: The application context
    """

    def __init__(self, context: "AppContext") -> None:
        """Initialize the QML application.

        Args:
            context: The application context containing library, facade,
                    and settings references.
        """
        self._context = context
        self._engine = QQmlApplicationEngine()

        # Register QML types and context properties
        self._register_types()
        self._register_controllers()

        # Load the main QML file
        self._load_main_qml()

    @property
    def engine(self) -> QQmlApplicationEngine:
        """The QML application engine."""
        return self._engine

    @property
    def context(self) -> "AppContext":
        """The application context."""
        return self._context

    def _register_types(self) -> None:
        """Register custom QML types.

        This method registers Python classes that need to be instantiated
        from QML using qmlRegisterType or qmlRegisterSingletonType.
        """
        # Import type registration utilities
        # from PySide6.QtQml import qmlRegisterType, qmlRegisterSingletonType

        # Example registrations (uncomment when implementing):
        # from .ui.widgets.crop_tool_item import CropToolItem
        # qmlRegisterType(CropToolItem, "iPhoto", 1, 0, "CropTool")

        # Note: Most controllers are registered as context properties
        # rather than QML types for singleton-like access.

    def _register_controllers(self) -> None:
        """Register Python controllers as QML context properties.

        Context properties are accessible from any QML file without imports
        and provide a clean way to expose singleton services.
        """
        from iPhoto.gui.ui.controllers.qml_controllers import (
            AlbumController,
            AssetController,
            EditSessionController,
            StatusController,
            ThemeController,
        )
        from iPhoto.gui.ui.models.album_tree_model import AlbumTreeModel
        from iPhoto.gui.ui.widgets.gallery_grid_view import ThumbnailImageProvider

        root = self._engine.rootContext()

        # Create models
        album_tree_model = AlbumTreeModel(self._context.library)
        asset_list_model = self._context.facade.asset_list_model

        # Create controllers
        theme_controller = ThemeController()
        album_controller = AlbumController(album_tree_model)
        asset_controller = AssetController(asset_list_model)
        status_controller = StatusController()
        edit_session_controller = EditSessionController()

        # Register as context properties
        root.setContextProperty("themeController", theme_controller)
        root.setContextProperty("albumController", album_controller)
        root.setContextProperty("assetController", asset_controller)
        root.setContextProperty("statusController", status_controller)
        root.setContextProperty("editSession", edit_session_controller)
        root.setContextProperty("importService", self._context.facade.import_service)

        # Expose icon path for dev environment (where qrc is not compiled)
        # Assuming icons are in ../ui/icon relative to this file
        icon_path = Path(__file__).parent / "ui" / "icon"
        root.setContextProperty("iconPrefix", QUrl.fromLocalFile(str(icon_path)).toString())

        # Also expose the facade and settings for advanced operations
        root.setContextProperty("facade", self._context.facade)
        root.setContextProperty("settings", self._context.settings)

        navigation_controller = _NavigationController(
            context=self._context,
            album_controller=album_controller,
            on_model_changed=self._update_asset_model,
        )
        dialog_controller = _DialogController(navigation_controller)

        root.setContextProperty("navigationController", navigation_controller)
        root.setContextProperty("dialogController", dialog_controller)

        # Register image provider for thumbnails
        self._thumbnail_provider = ThumbnailImageProvider()
        self._thumbnail_provider.set_model(asset_list_model)
        self._engine.addImageProvider("thumbnails", self._thumbnail_provider)

        # Keep controllers alive
        self._controllers = {
            "theme": theme_controller,
            "album": album_controller,
            "asset": asset_controller,
            "status": status_controller,
            "editSession": edit_session_controller,
            "navigation": navigation_controller,
            "dialog": dialog_controller,
        }
        self._models = {
            "albumTree": album_tree_model,
            "assetList": asset_list_model,
        }

        # Track active model changes from the facade
        self._context.facade.activeModelChanged.connect(self._update_asset_model)

        # Kick off initial load if a library is already bound
        navigation_controller.open_default_album()

    def _load_main_qml(self) -> None:
        """Load the main QML file."""
        main_qml = QML_DIR / "main.qml"

        if not main_qml.exists():
            raise FileNotFoundError(
                f"Main QML file not found: {main_qml}\n"
                "Ensure the QML files are properly installed."
            )

        # Add the QML directory to import paths
        self._engine.addImportPath(str(QML_DIR))
        self._engine.addImportPath(str(QML_DIR.parent))

        # Load the main QML file
        self._engine.load(QUrl.fromLocalFile(str(main_qml)))

        # Check for loading errors
        if not self._engine.rootObjects():
            raise RuntimeError(
                "Failed to load main.qml. Check the QML console for errors."
            )

    def root_window(self):
        """Return the root window object if available."""
        objects = self._engine.rootObjects()
        return objects[0] if objects else None

    def _update_asset_model(self, model) -> None:
        """Update QML bindings when the active asset model switches."""
        controller = self._controllers.get("asset")
        if controller is not None:
            controller.setModel(model)  # type: ignore[attr-defined]
        provider = getattr(self, "_thumbnail_provider", None)
        if provider is not None:
            provider.set_model(model)


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtCore import qInstallMessageHandler, QtMsgType

    def qml_message_handler(msg_type: QtMsgType, context, message):
        prefix = {
            QtMsgType.QtDebugMsg: "Debug",
            QtMsgType.QtInfoMsg: "Info",
            QtMsgType.QtWarningMsg: "Warning",
            QtMsgType.QtCriticalMsg: "Critical",
            QtMsgType.QtFatalMsg: "Fatal",
        }.get(msg_type, "Unknown")
        print(f"[QML {prefix}] {message}")

    qInstallMessageHandler(qml_message_handler)

    arguments = list(sys.argv if argv is None else argv)
    app = QGuiApplication(arguments)

    from iPhoto.appctx import AppContext
    context = AppContext()

    try:
        qml_app = QMLApplication(context)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error initializing QML application: {e}", file=sys.stderr)
        return 1

    return app.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
