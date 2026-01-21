import logging
from typing import Optional
from PySide6.QtCore import QObject
from src.iPhoto.gui.appctx import AppContext
from src.iPhoto.gui.viewmodels.album_viewmodel import AlbumViewModel
from src.iPhoto.application.services.album_service import AlbumService
from src.iPhoto.application.services.asset_service import AssetService

# Import UI components if available or define placeholders
# For now, we assume this orchestrates high level logic

class MainCoordinator(QObject):
    def __init__(self, context: AppContext):
        super().__init__()
        self._context = context
        self._logger = logging.getLogger(__name__)
        self._active_album_vm: Optional[AlbumViewModel] = None

    def start(self):
        self._logger.info("MainCoordinator started")
        # Logic to show initial window, e.g. Library

    def open_album(self, path):
        self._logger.info(f"Coordinator opening album: {path}")
        album_service = self._context.container.resolve(AlbumService)
        asset_service = self._context.container.resolve(AssetService)

        vm = AlbumViewModel(album_service, asset_service)
        vm.load_album(path)

        self._active_album_vm = vm
        # Signal view to update...

        return vm
