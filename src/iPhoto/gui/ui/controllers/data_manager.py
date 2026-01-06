"""Data-centric helpers used by the main window controller."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt

from ..media import MediaController, PlaylistController
from ..models.asset_model import AssetModel
from ..models.spacer_proxy_model import SpacerProxyModel
from ..widgets.asset_delegate import AssetGridDelegate

if TYPE_CHECKING:  # pragma: no cover - import used for typing only
    from ..ui_main_window import Ui_MainWindow
    from ...facade import AppFacade
    from ..main_window import MainWindow
    from ..models.asset_list.model import AssetListModel

logger = logging.getLogger(__name__)


class DataManager(QObject):
    """Own the core models and data controllers used across the UI.
    
    Implements a Dual-Proxy Architecture where separate proxy instances
    are maintained for the Library and Album contexts. This eliminates
    the expensive setSourceModel() call during view switching, reducing
    latency to a near-zero pointer assignment operation.
    """

    def __init__(self, window: "MainWindow", facade: "AppFacade") -> None:
        super().__init__(window)
        self._window = window
        self._facade = facade
        self._ui: "Ui_MainWindow | None" = None

        # Dual-Proxy Architecture: Create independent proxy instances
        # for Library and Album contexts to enable O(1) view switching.
        
        # Library Proxy: Permanently attached to _library_list_model
        # Remains sorted and filtered in memory even when not visible
        self._library_proxy = AssetModel(self._facade)
        self._library_proxy.setSourceModel(self._facade._library_list_model)
        
        # Album Proxy: Permanently attached to _album_list_model
        self._album_proxy = AssetModel(self._facade)
        self._album_proxy.setSourceModel(self._facade._album_list_model)
        
        # Track the active proxy (default to library for "All Photos" initial view)
        self._active_proxy: AssetModel = self._library_proxy
        
        # For backward compatibility, _asset_model references the active proxy
        self._asset_model = self._active_proxy
        
        # Connect to context changes for instant proxy switching
        self._facade.activeModelChanged.connect(self._on_active_model_changed)

        # Filmstrip model follows the active proxy
        self._filmstrip_model = SpacerProxyModel(window)
        self._filmstrip_model.setSourceModel(self._active_proxy)

        self._media = MediaController(window)
        self._playlist = PlaylistController(window)
        self._grid_delegate: AssetGridDelegate | None = None

    # ------------------------------------------------------------------
    # Dual-Proxy Switching Logic
    # ------------------------------------------------------------------
    def _on_active_model_changed(self, target_source_model: "AssetListModel") -> None:
        """Switch the View's model reference directly for O(1) context switching.
        
        Instead of calling setSourceModel() on a single proxy (which triggers
        expensive remapping and re-sorting), we simply swap the entire proxy
        instance that the view points to. This eliminates computation during
        navigation.
        
        Args:
            target_source_model: The source model that is now active (either
                _library_list_model or _album_list_model from the facade).
        """
        # Determine which proxy corresponds to the target source
        if target_source_model is self._facade._library_list_model:
            new_proxy = self._library_proxy
        else:
            new_proxy = self._album_proxy
        
        # Skip if already on the correct proxy (no-op optimization)
        if new_proxy is self._active_proxy:
            return
        
        logger.info(
            "Dual-Proxy switch: %s -> %s",
            "library" if self._active_proxy is self._library_proxy else "album",
            "library" if new_proxy is self._library_proxy else "album",
        )
        
        # Update tracking references
        self._active_proxy = new_proxy
        self._asset_model = new_proxy
        
        # Swap model on the view (O(1) pointer assignment)
        if self._ui is not None:
            self._ui.grid_view.setModel(new_proxy)
            
            # Re-attach delegate to ensure proper rendering
            if self._grid_delegate is not None:
                self._ui.grid_view.setItemDelegate(self._grid_delegate)
        
        # Update filmstrip to follow the active proxy
        self._filmstrip_model.setSourceModel(new_proxy)

    # ------------------------------------------------------------------
    # Model exposure
    def asset_model(self) -> AssetModel:
        """Return the currently active proxy model for the gallery."""
        return self._active_proxy
    
    def library_proxy(self) -> AssetModel:
        """Return the persistent library proxy (for "All Photos" view)."""
        return self._library_proxy
    
    def album_proxy(self) -> AssetModel:
        """Return the persistent album proxy (for physical album views)."""
        return self._album_proxy

    def filmstrip_model(self) -> SpacerProxyModel:
        """Return the proxy model used by the filmstrip view."""

        return self._filmstrip_model

    def media(self) -> MediaController:
        """Expose the multimedia backend wrapper."""

        return self._media

    def playlist(self) -> PlaylistController:
        """Expose the playlist controller shared between the views."""

        return self._playlist

    def grid_delegate(self) -> AssetGridDelegate | None:
        """Return the grid delegate once it has been created."""

        return self._grid_delegate

    # ------------------------------------------------------------------
    # View configuration helpers
    def configure_views(self, ui: "Ui_MainWindow") -> None:
        """Attach models and delegates to the widgets constructed by the UI."""
        
        # Store UI reference for proxy switching
        self._ui = ui

        ui.grid_view.setModel(self._active_proxy)
        self._grid_delegate = AssetGridDelegate(ui.grid_view)
        ui.grid_view.setItemDelegate(self._grid_delegate)
        ui.grid_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        ui.filmstrip_view.setModel(self._filmstrip_model)
        ui.filmstrip_view.setItemDelegate(
            AssetGridDelegate(ui.filmstrip_view, filmstrip_mode=True)
        )

        ui.video_area.hide_controls(animate=False)
        self._media.set_video_output(ui.video_area.video_item)

        ui.player_bar.setEnabled(False)
        ui.selection_button.setEnabled(False)

