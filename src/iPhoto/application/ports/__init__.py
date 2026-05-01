"""Application-level ports for vNext runtime boundaries."""

from .media import (
    EditSidecarPort,
    LocationMetadataPort,
    MediaScannerPort,
    MetadataReaderPort,
    MetadataWriterPort,
    ThumbnailRendererPort,
)
from .people import PeopleAssetRepositoryPort, PeopleIndexPort
from .repositories import (
    AlbumRepositoryPort,
    AssetFavoriteQueryPort,
    AssetRepositoryPort,
    LibraryStateRepositoryPort,
)
from .runtime import (
    MapBackendKind,
    MapRuntimeCapabilities,
    MapRuntimePort,
    TaskSchedulerPort,
)

__all__ = [
    "AlbumRepositoryPort",
    "AssetRepositoryPort",
    "AssetFavoriteQueryPort",
    "EditSidecarPort",
    "LibraryStateRepositoryPort",
    "LocationMetadataPort",
    "MapBackendKind",
    "MapRuntimeCapabilities",
    "MapRuntimePort",
    "MediaScannerPort",
    "MetadataReaderPort",
    "MetadataWriterPort",
    "PeopleIndexPort",
    "PeopleAssetRepositoryPort",
    "TaskSchedulerPort",
    "ThumbnailRendererPort",
]
