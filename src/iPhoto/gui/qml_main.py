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
from typing import TYPE_CHECKING

# Ensure the src directory is in the Python path when running directly
_SRC_DIR = Path(__file__).resolve().parent.parent.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

if TYPE_CHECKING:
    from iPhoto.appctx import AppContext

# Path to the QML directory
QML_DIR = Path(__file__).parent / "qml"


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
        from iPhoto.gui.ui.models.asset_model import AssetListModel

        root = self._engine.rootContext()

        # Create models
        album_tree_model = AlbumTreeModel(self._context.library)
        asset_list_model = AssetListModel(self._context.facade)

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

        # Expose icon path for dev environment (where qrc is not compiled)
        # Assuming icons are in ../ui/icon relative to this file
        icon_path = Path(__file__).parent / "ui" / "icon"
        root.setContextProperty("iconPrefix", QUrl.fromLocalFile(str(icon_path)).toString())

        # Also expose the facade and settings for advanced operations
        root.setContextProperty("facade", self._context.facade)
        root.setContextProperty("settings", self._context.settings)

        # Store references to prevent garbage collection
        self._controllers = {
            "theme": theme_controller,
            "album": album_controller,
            "asset": asset_controller,
            "status": status_controller,
            "editSession": edit_session_controller,
        }
        self._models = {
            "albumTree": album_tree_model,
            "assetList": asset_list_model,
        }

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


def main(argv: list[str] | None = None) -> int:
    """Launch the QML application and return the exit code.

    This is the main entry point for the QML-based GUI. It creates
    the application context, initializes the QML engine, and starts
    the event loop.

    Args:
        argv: Command line arguments. If None, uses sys.argv.

    Returns:
        The application exit code.
    """
    from iPhoto.appctx import AppContext

    arguments = list(sys.argv if argv is None else argv)

    # Set the Quick Controls style to Basic to allow customization
    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"

    # Use QGuiApplication for pure QML (no QWidget)
    # If mixing with QWidgets, use QApplication instead
    app = QGuiApplication(arguments)

    # Create application context
    context = AppContext()

    # Create and initialize the QML application
    try:
        qml_app = QMLApplication(context)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error initializing QML application: {e}", file=sys.stderr)
        return 1

    # Handle command line arguments
    if len(arguments) > 1:
        # Open album specified in argv[1]
        album_path = Path(arguments[1])
        if album_path.exists():
            qml_app._controllers["album"].selectAlbum(str(album_path))
        else:
            print(f"Warning: Album path does not exist: {album_path}", file=sys.stderr)

    return app.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
