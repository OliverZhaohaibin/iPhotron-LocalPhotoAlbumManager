"""Tests for the runtime-owned library asset services."""

from __future__ import annotations

from pathlib import Path

import pytest

from iPhoto.cache.index_store import reset_global_repository
from iPhoto.infrastructure.repositories.index_store_asset_repository import (
    IndexStoreAssetRepositoryAdapter,
)
from iPhoto.infrastructure.services.library_asset_runtime import LibraryAssetRuntime


@pytest.fixture(autouse=True)
def _reset_global_index() -> None:
    reset_global_repository()
    yield
    reset_global_repository()


def test_bind_library_root_rebuilds_repo_and_cache_path(tmp_path: Path) -> None:
    initial_root = tmp_path / "initial"
    initial_root.mkdir()
    runtime = LibraryAssetRuntime(initial_root)
    initial_repository = runtime.repository
    initial_assets = runtime.assets

    library_root = tmp_path / "library"
    library_root.mkdir()

    runtime.bind_library_root(library_root)

    assert runtime.repository is not initial_repository
    assert runtime.assets is not initial_assets
    assert isinstance(runtime.repository, IndexStoreAssetRepositoryAdapter)
    assert runtime.repository.index_store is runtime.assets
    assert runtime.thumbnail_service._disk_cache_path == (
        library_root / ".iPhoto" / "cache" / "thumbs"
    )

    runtime.shutdown()


def test_bind_library_root_uses_existing_legacy_work_dir(tmp_path: Path) -> None:
    initial_root = tmp_path / "initial"
    initial_root.mkdir()
    runtime = LibraryAssetRuntime(initial_root)
    library_root = tmp_path / "library"
    legacy_work_dir = library_root / ".iphoto"
    legacy_work_dir.mkdir(parents=True)

    runtime.bind_library_root(library_root)

    assert runtime.thumbnail_service._disk_cache_path == (
        legacy_work_dir / "cache" / "thumbs"
    )
    assert runtime.assets.path == legacy_work_dir / "global_index.db"
    assert runtime.repository.index_store is runtime.assets

    runtime.shutdown()
