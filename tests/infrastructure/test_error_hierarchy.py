"""Tests for the custom error hierarchy (Phase 1/2 refactoring)."""

import pytest
from iPhoto.errors import (
    IPhotoError,
    DomainError,
    InfrastructureError,
    ApplicationError,
    AlbumNotFoundError,
    AssetNotFoundError,
    DatabaseError,
    ConnectionPoolExhausted,
    ScanError,
    ImportError_,
    CircularDependencyError,
    ResolutionError,
)


def test_domain_error_is_iphoto_error():
    assert issubclass(DomainError, IPhotoError)
    assert isinstance(DomainError("x"), IPhotoError)


def test_infrastructure_error_is_iphoto_error():
    assert issubclass(InfrastructureError, IPhotoError)
    assert isinstance(InfrastructureError("x"), IPhotoError)


def test_application_error_is_iphoto_error():
    assert issubclass(ApplicationError, IPhotoError)
    assert isinstance(ApplicationError("x"), IPhotoError)


def test_album_not_found_is_domain_error():
    assert issubclass(AlbumNotFoundError, DomainError)
    assert isinstance(AlbumNotFoundError("missing"), DomainError)


def test_connection_pool_exhausted_is_infrastructure_error():
    assert issubclass(ConnectionPoolExhausted, InfrastructureError)
    assert isinstance(ConnectionPoolExhausted("full"), InfrastructureError)


def test_scan_error_is_application_error():
    assert issubclass(ScanError, ApplicationError)
    assert isinstance(ScanError("failed"), ApplicationError)


def test_circular_dependency_is_iphoto_error():
    assert issubclass(CircularDependencyError, IPhotoError)
    assert isinstance(CircularDependencyError("loop"), IPhotoError)


def test_error_message():
    err = AlbumNotFoundError("album-42 not found")
    assert str(err) == "album-42 not found"
