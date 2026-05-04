"""Compatibility helpers for standalone album access outside a bound session."""

from __future__ import annotations

from pathlib import Path

from .service_factories import (
    create_compat_album_metadata_service,
    create_compat_asset_lifecycle_service,
    create_compat_asset_operation_service,
    create_compat_asset_query_service,
    create_compat_scan_service,
)


def create_standalone_asset_query_service(root: Path):
    """Return a query service for an album opened without a bound session."""

    return create_compat_asset_query_service(Path(root))


def create_standalone_asset_lifecycle_service(
    root: Path | None,
    *,
    scan_service=None,
):
    """Return a lifecycle service for a standalone album workflow."""

    return create_compat_asset_lifecycle_service(root, scan_service=scan_service)


def create_standalone_asset_operation_service(
    root: Path | None,
    *,
    lifecycle_service=None,
):
    """Return an operation service for a standalone album workflow."""

    return create_compat_asset_operation_service(
        root,
        lifecycle_service=lifecycle_service,
    )


def create_standalone_album_metadata_service(
    root: Path | None,
    *,
    state_repository=None,
):
    """Return an album metadata service for a standalone album workflow."""

    return create_compat_album_metadata_service(
        root,
        state_repository=state_repository,
    )


def create_standalone_scan_service(root: Path):
    """Return a scan service for an album opened without a bound session."""

    return create_compat_scan_service(Path(root))


__all__ = [
    "create_standalone_album_metadata_service",
    "create_standalone_asset_lifecycle_service",
    "create_standalone_asset_operation_service",
    "create_standalone_asset_query_service",
    "create_standalone_scan_service",
]
