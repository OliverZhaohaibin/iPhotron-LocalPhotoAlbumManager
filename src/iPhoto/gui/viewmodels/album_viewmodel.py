import logging
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from src.iPhoto.application.services.album_service import AlbumService
from src.iPhoto.application.services.asset_service import AssetService
from src.iPhoto.domain.models.query import AssetQuery

class AlbumViewModel(QObject):
    albumLoaded = Signal(object) # Payload: Album DTO or similar
    assetsLoaded = Signal(list)
    scanFinished = Signal()

    def __init__(self, album_service: AlbumService, asset_service: AssetService):
        super().__init__()
        self._album_service = album_service
        self._asset_service = asset_service
        self._logger = logging.getLogger(__name__)
        self._current_album_id = None

    def load_album(self, path: Path):
        try:
            # 1. Open Album
            response = self._album_service.open_album(path)
            self._current_album_id = response.album_id
            self.albumLoaded.emit(response)

            # 2. Trigger Scan (async typically, but sync for now or via service)
            # self._album_service.scan_album(response.album_id)

            # 3. Load Assets
            self.refresh_assets()

        except Exception as e:
            self._logger.error(f"Failed to load album: {e}")

    def refresh_assets(self):
        if not self._current_album_id:
            return

        query = AssetQuery().with_album_id(self._current_album_id)
        assets = self._asset_service.find_assets(query)
        self.assetsLoaded.emit(assets)

    def scan_current_album(self):
        if self._current_album_id:
            self._album_service.scan_album(self._current_album_id)
            self.refresh_assets()
            self.scanFinished.emit()
