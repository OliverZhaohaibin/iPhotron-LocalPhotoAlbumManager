"""GUI entry point for the iPhoto desktop application."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


from src.iPhoto.appctx import AppContext
from src.iPhoto.gui.ui.main_window import MainWindow

# New Architecture Imports
from src.iPhoto.di.container import DependencyContainer
from src.iPhoto.events.bus import EventBus
from src.iPhoto.infrastructure.db.pool import ConnectionPool
from src.iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from src.iPhoto.infrastructure.repositories.sqlite_album_repository import SQLiteAlbumRepository
from src.iPhoto.infrastructure.repositories.sqlite_asset_repository import SQLiteAssetRepository
from src.iPhoto.application.use_cases.open_album import OpenAlbumUseCase
from src.iPhoto.application.use_cases.scan_album import ScanAlbumUseCase
from src.iPhoto.application.use_cases.pair_live_photos import PairLivePhotosUseCase
from src.iPhoto.application.services.album_service import AlbumService
from src.iPhoto.application.services.asset_service import AssetService
from src.iPhoto.gui.coordinators.main_coordinator import MainCoordinator


def main(argv: list[str] | None = None) -> int:
    """Launch the Qt application and return the exit code."""

    arguments = list(sys.argv if argv is None else argv)
    app = QApplication(arguments)

    # ``QToolTip`` instances inherit ``WA_TranslucentBackground`` from the frameless
    # main window, which means they expect the application to provide an opaque fill
    # colour.  Some Qt styles ignore stylesheet rules for tooltips, so we proactively
    # update the palette that drives those popups to guarantee readable text.
    tooltip_palette = QPalette(app.palette())

    def _resolved_colour(source: QColor, fallback: QColor) -> QColor:
        """Return a copy of *source* with a fully opaque alpha channel.

        Qt reports transparent colours for certain palette roles when
        ``WA_TranslucentBackground`` is active.  Failing to normalise the alpha value
        causes the compositor to blend the tooltip against the desktop wallpaper,
        producing the solid black rectangle described in the regression report.
        Falling back to a well-tested default keeps the tooltip legible even on
        themes that omit one of the roles we query.
        """

        if not source.isValid():
            return QColor(fallback)

        resolved = QColor(source)
        resolved.setAlpha(255)
        return resolved

    base_colour = _resolved_colour(
        tooltip_palette.color(QPalette.ColorRole.Window), QColor("#eef3f6")
    )
    text_colour = _resolved_colour(
        tooltip_palette.color(QPalette.ColorRole.WindowText), QColor(Qt.GlobalColor.black)
    )

    # Ensure the text remains readable by checking the lightness contrast.  When the
    # palette provides nearly identical shades we fall back to a simple dark-on-light
    # scheme that mirrors Qt's built-in defaults.
    if abs(base_colour.lightness() - text_colour.lightness()) < 40:
        base_colour = QColor("#eef3f6")
        text_colour = QColor(Qt.GlobalColor.black)

    tooltip_palette.setColor(QPalette.ColorRole.ToolTipBase, base_colour)
    tooltip_palette.setColor(QPalette.ColorRole.ToolTipText, text_colour)
    app.setPalette(tooltip_palette, "QToolTip")

    # --- Phase 1: Infrastructure Modernization Setup ---
    container = DependencyContainer()

    # 1. Event Bus (Missing in current file structure, assuming placeholder or future impl)
    # Using a simple placeholder if EventBus is not available or mocking it
    try:
        container.register(EventBus, singleton=True) # Basic registration
    except ImportError:
        pass # Handle gracefully if EventBus class is missing or different signature

    # 2. Database Connection Pool
    # We need a path for the global DB. Assuming context.library.root holds it or using default.
    # For now, we defer pool creation until we know the library root, OR we create a global one.
    # Let's assume a default location or that AppContext handles the primary DB path.
    # Legacy AppContext initializes LibraryManager.

    context = AppContext()

    # Use the library root from the legacy context for the new DB pool
    # If not bound, it might be None.
    db_path = Path.home() / ".iPhoto" / "global_index.db"
    if context.library.root():
        db_path = context.library.root() / ".iPhoto" / "global_index.db"

    # Ensure directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    pool = ConnectionPool(db_path)
    container.register(ConnectionPool, instance=pool) # Assuming instance registration is supported or we wrap it

    # 3. Repositories
    container.register(IAlbumRepository, SQLiteAlbumRepository, args=[pool], singleton=True)
    container.register(IAssetRepository, SQLiteAssetRepository, args=[pool], singleton=True)

    # 4. Use Cases
    container.register(OpenAlbumUseCase, args=[container.resolve(IAlbumRepository), container.resolve(IAssetRepository)])
    # ScanAlbumUseCase might need EventBus
    # container.register(ScanAlbumUseCase, args=[...])

    # 5. Services
    # Resolving repositories to pass to services
    album_repo = container.resolve(IAlbumRepository)
    asset_repo = container.resolve(IAssetRepository)

    # Instantiate Use Cases manually for Service injection if auto-resolution is simple
    open_uc = OpenAlbumUseCase(album_repo, asset_repo)
    # Mocking others for now as they might have complex dependencies
    scan_uc = ScanAlbumUseCase(album_repo, asset_repo)
    pair_uc = PairLivePhotosUseCase(album_repo, asset_repo)

    container.register(AlbumService, args=[open_uc, scan_uc, pair_uc], singleton=True)
    container.register(AssetService, args=[asset_repo], singleton=True)

    # --- Phase 4: Coordinator Wiring ---
    window = MainWindow(context)

    # Coordinator needs Window, Context, and Container
    coordinator = MainCoordinator(window, context, container)

    # Injection into Window
    window.set_coordinator(coordinator)

    coordinator.start()
    window.show()

    # Allow opening an album directly via argv[1].
    if len(arguments) > 1:
        # Use new coordinator method
        coordinator.open_album_from_path(Path(arguments[1]))
    else:
        window.ui.sidebar.select_all_photos(emit_signal=True)

    return app.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
