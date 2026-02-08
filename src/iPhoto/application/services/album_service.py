import logging
from pathlib import Path
from typing import List, Optional

from src.iPhoto.application.use_cases.open_album import OpenAlbumUseCase
from src.iPhoto.application.use_cases.scan_album import ScanAlbumUseCase
from src.iPhoto.application.use_cases.pair_live_photos import PairLivePhotosUseCase
from src.iPhoto.application.dtos import (
    OpenAlbumRequest, OpenAlbumResponse,
    ScanAlbumRequest, ScanAlbumResponse,
    PairLivePhotosRequest, PairLivePhotosResponse
)

class AlbumService:
    """
    Application Service Facade for Album operations.
    Delegates to specific Use Cases.
    """
    def __init__(
        self,
        open_album_use_case: OpenAlbumUseCase,
        scan_album_use_case: ScanAlbumUseCase,
        pair_live_photos_use_case: PairLivePhotosUseCase
    ):
        self._open_album_uc = open_album_use_case
        self._scan_album_uc = scan_album_use_case
        self._pair_live_uc = pair_live_photos_use_case
        self._logger = logging.getLogger(__name__)

    def open_album(self, path: Path) -> OpenAlbumResponse:
        request = OpenAlbumRequest(path=path)
        return self._open_album_uc.execute(request)

    def scan_album(self, album_id: str, force_rescan: bool = False) -> ScanAlbumResponse:
        request = ScanAlbumRequest(album_id=album_id, force_rescan=force_rescan)
        return self._scan_album_uc.execute(request)

    def pair_live_photos(self, album_id: str) -> PairLivePhotosResponse:
        request = PairLivePhotosRequest(album_id=album_id)
        return self._pair_live_uc.execute(request)
