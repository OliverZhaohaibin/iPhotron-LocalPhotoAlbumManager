"""Tests for the runtime-owned library asset services."""

from __future__ import annotations

from pathlib import Path

from iPhoto.infrastructure.services.library_asset_runtime import LibraryAssetRuntime


def test_bind_library_root_rebuilds_repo_and_cache_path(tmp_path: Path) -> None:
    runtime = LibraryAssetRuntime()
    initial_repository = runtime.repository

    library_root = tmp_path / "library"
    library_root.mkdir()

    runtime.bind_library_root(library_root)

    assert runtime.repository is not initial_repository
    assert runtime.thumbnail_service._disk_cache_path == (
        library_root / ".iPhoto" / "cache" / "thumbs"
    )

    runtime.shutdown()
