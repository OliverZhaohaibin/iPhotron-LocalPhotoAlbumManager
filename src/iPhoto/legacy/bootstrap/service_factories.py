"""Explicit compatibility factories for headless/sessionless entry points."""

from __future__ import annotations

from pathlib import Path


def create_compat_scan_service(root: Path):
    """Create a scan service for legacy callers without an active session."""

    from iPhoto.bootstrap.library_scan_service import LibraryScanService

    return LibraryScanService(Path(root))


def create_compat_asset_query_service(root: Path, *, repository_factory=None):
    """Create an asset query service for legacy callers without an active session."""

    from iPhoto.bootstrap.library_asset_query_service import LibraryAssetQueryService

    if repository_factory is None:
        return LibraryAssetQueryService(Path(root))
    return LibraryAssetQueryService(Path(root), repository_factory=repository_factory)


def create_compat_asset_lifecycle_service(root: Path | None, *, scan_service=None):
    """Create a lifecycle service for legacy callers without an active session."""

    from iPhoto.bootstrap.library_asset_lifecycle_service import LibraryAssetLifecycleService

    return LibraryAssetLifecycleService(
        Path(root) if root is not None else None,
        scan_service=scan_service,
    )


def create_compat_asset_operation_service(root: Path | None, *, lifecycle_service=None):
    """Create an operation service for legacy callers without an active session."""

    from iPhoto.bootstrap.library_asset_operation_service import LibraryAssetOperationService

    return LibraryAssetOperationService(
        Path(root) if root is not None else None,
        lifecycle_service=lifecycle_service,
    )


def create_compat_album_metadata_service(root: Path | None, *, state_repository=None):
    """Create an album metadata service for legacy callers without an active session."""

    from iPhoto.bootstrap.library_album_metadata_service import LibraryAlbumMetadataService

    return LibraryAlbumMetadataService(
        Path(root) if root is not None else None,
        state_repository=state_repository,
    )


def create_compat_location_service(root: Path, *, query_service=None):
    """Create a location service for legacy callers without an active session."""

    from iPhoto.bootstrap.library_location_service import LibraryLocationService

    return LibraryLocationService(Path(root), query_service=query_service)
