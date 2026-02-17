# export_assets.py
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .base import UseCase, UseCaseRequest, UseCaseResponse
from iPhoto.domain.repositories import IAlbumRepository, IAssetRepository
from iPhoto.events.bus import EventBus


@dataclass(frozen=True)
class ExportAssetsRequest(UseCaseRequest):
    asset_ids: list[str] = field(default_factory=list)
    export_dir: str = ""
    album_id: str = ""


@dataclass(frozen=True)
class ExportAssetsResponse(UseCaseResponse):
    exported_count: int = 0
    failed_count: int = 0
    exported_paths: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)


class ExportAssetsUseCase(UseCase):
    """Exports assets to a target directory with collision-safe naming.

    When a ``render_fn`` is provided, edited images are rendered through
    it before being written to the export directory.  If ``render_fn`` is
    *None* or returns *None* for a given source path, the original file
    is copied as-is via :func:`shutil.copy2`.
    """

    def __init__(
        self,
        asset_repo: IAssetRepository,
        album_repo: IAlbumRepository,
        event_bus: EventBus,
        render_fn=None,
    ):
        self._asset_repo = asset_repo
        self._album_repo = album_repo
        self._event_bus = event_bus
        self._render_fn = render_fn
        self._logger = logging.getLogger(__name__)

    def execute(self, request: ExportAssetsRequest) -> ExportAssetsResponse:
        export_dir = Path(request.export_dir)
        if not export_dir.is_dir():
            try:
                export_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return ExportAssetsResponse(
                    success=False,
                    error=f"Cannot create export directory: {exc}",
                )

        exported = []
        failed = []

        for asset_id in request.asset_ids:
            asset = self._asset_repo.get(asset_id)
            if asset is None:
                failed.append(asset_id)
                continue

            album = self._album_repo.get(asset.album_id)
            if album is None:
                failed.append(asset_id)
                continue

            src = album.path / asset.path
            if not src.exists():
                failed.append(str(src))
                continue

            try:
                rendered = None
                if self._render_fn is not None:
                    rendered = self._render_fn(src)

                if rendered is not None:
                    dst = self._unique_dest(export_dir / (src.stem + ".jpg"))
                    dst.write_bytes(rendered)
                else:
                    dst = self._unique_dest(export_dir / src.name)
                    shutil.copy2(str(src), str(dst))
                exported.append(str(dst))
            except Exception as exc:
                self._logger.error("Export failed for %s: %s", src, exc)
                failed.append(str(src))

        return ExportAssetsResponse(
            exported_count=len(exported),
            failed_count=len(failed),
            exported_paths=exported,
            failed_paths=failed,
        )

    @staticmethod
    def _unique_dest(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        counter = 1
        while True:
            candidate = path.parent / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1
