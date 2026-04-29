"""Tests for the runtime-owned library asset services."""

from __future__ import annotations

from pathlib import Path

from iPhoto.infrastructure.services.library_asset_runtime import LibraryAssetRuntime


def test_bind_library_root_rebuilds_repo_and_cache_path(tmp_path: Path) -> None:
    initial_root = tmp_path / "initial"
    initial_root.mkdir()
    runtime = LibraryAssetRuntime(initial_root)
    initial_repository = runtime.repository

    library_root = tmp_path / "library"
    library_root.mkdir()

    runtime.bind_library_root(library_root)

    assert runtime.repository is not initial_repository
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
    assert runtime._pool is not None
    assert runtime._pool._db_path == legacy_work_dir / "global_index.db"

    runtime.shutdown()
