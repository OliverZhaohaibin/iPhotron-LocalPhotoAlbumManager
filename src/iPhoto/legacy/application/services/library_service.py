"""Application Service for library-level operations.

Integrates :class:`ParallelScanner` for concurrent album scanning while
keeping CPU pressure low enough that the UI thread stays responsive.
"""

import logging
from pathlib import Path
from typing import Generator, List, Optional

from iPhoto.domain.models.core import Asset
from iPhoto.legacy.domain.repositories import IAlbumRepository, IAssetRepository
from iPhoto.legacy.application.use_cases.create_album import CreateAlbumUseCase, CreateAlbumRequest
from iPhoto.legacy.application.use_cases.delete_album import DeleteAlbumUseCase, DeleteAlbumRequest
from iPhoto.legacy.application.services.parallel_scanner import ParallelScanner, ScanResult
from iPhoto.events.bus import EventBus


class LibraryService:
    """Application Service for library-level operations.

    Provides album CRUD plus CPU-aware parallel scanning that publishes
    streaming progress events so the UI can display results incrementally.
    """

    def __init__(
        self,
        album_repo: IAlbumRepository,
        create_album_uc: CreateAlbumUseCase,
        delete_album_uc: DeleteAlbumUseCase,
        parallel_scanner: ParallelScanner | None = None,
        asset_repo: IAssetRepository | None = None,
        event_bus: EventBus | None = None,
    ):
        self._album_repo = album_repo
        self._create_album_uc = create_album_uc
        self._delete_album_uc = delete_album_uc
        self._scanner = parallel_scanner
        self._asset_repo = asset_repo
        self._event_bus = event_bus
        self._logger = logging.getLogger(__name__)

    # -- Album CRUD --------------------------------------------------------

    def create_album(self, path: Path, title: str = "") -> str:
        response = self._create_album_uc.execute(CreateAlbumRequest(path=path, title=title))
        if not response.success:
            raise ValueError(response.error)
        return response.album_id

    def delete_album(self, album_id: str) -> None:
        response = self._delete_album_uc.execute(DeleteAlbumRequest(album_id=album_id))
        if not response.success:
            raise ValueError(response.error)

    # -- Parallel scanning -------------------------------------------------

    def scan_album_parallel(self, album_path: Path) -> ScanResult:
        """Run a CPU-aware parallel scan and return all results at once.

        Uses :class:`ParallelScanner` with a worker count of
        ``cpu_count // 2`` by default to avoid starving the UI thread.
        Falls back to an empty result if no scanner is configured.
        """
        if self._scanner is None:
            return ScanResult()
        return self._scanner.scan(album_path)

    def scan_album_streaming(
        self, album_path: Path
    ) -> Generator[ScanResult, None, None]:
        """Stream partial scan results so the UI can load assets incrementally.

        Yields a :class:`ScanResult` after every batch.  The caller can
        iterate and update the UI after each yield without waiting for the
        full scan to finish.  If an ``asset_repo`` was provided, each batch
        is also persisted immediately.
        """
        if self._scanner is None:
            return

        for batch in self._scanner.scan_streaming(album_path):
            # Persist the batch to the repository as it arrives
            if self._asset_repo and batch.assets:
                try:
                    self._asset_repo.save_batch(batch.assets)
                except Exception:
                    self._logger.exception("Failed to persist scan batch")
            yield batch

    def cancel_scan(self) -> None:
        """Abort an in-progress parallel scan."""
        if self._scanner is not None:
            self._scanner.cancel()
