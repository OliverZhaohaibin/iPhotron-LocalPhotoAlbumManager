"""Compatibility exports for legacy domain-repository use cases.

New runtime flows should prefer session/application ports such as
``ScanLibraryUseCase`` and library-scoped services instead of this package.
"""

from .base import UseCase, UseCaseRequest, UseCaseResponse
from .open_album import OpenAlbumUseCase
from .scan_album import ScanAlbumUseCase
from .pair_live_photos import PairLivePhotosUseCase
from .import_assets import ImportAssetsUseCase, ImportAssetsRequest, ImportAssetsResponse
from .create_album import CreateAlbumUseCase, CreateAlbumRequest, CreateAlbumResponse
from .delete_album import DeleteAlbumUseCase, DeleteAlbumRequest, DeleteAlbumResponse
from .move_assets import MoveAssetsUseCase, MoveAssetsRequest, MoveAssetsResponse
from .update_metadata import UpdateMetadataUseCase, UpdateMetadataRequest, UpdateMetadataResponse
from .generate_thumbnail import GenerateThumbnailUseCase, GenerateThumbnailRequest, GenerateThumbnailResponse
from .manage_trash import ManageTrashUseCase, ManageTrashRequest, ManageTrashResponse
from .aggregate_geo_data import AggregateGeoDataUseCase, AggregateGeoDataRequest, AggregateGeoDataResponse
from .watch_filesystem import WatchFilesystemUseCase, WatchFilesystemRequest, WatchFilesystemResponse
from .export_assets import ExportAssetsUseCase, ExportAssetsRequest, ExportAssetsResponse
from .apply_edit import ApplyEditUseCase, ApplyEditRequest, ApplyEditResponse
