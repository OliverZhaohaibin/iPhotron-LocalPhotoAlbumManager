from pathlib import Path

from src.iPhoto.cache.index_store import get_global_repository, reset_global_repository
from src.iPhoto.cache.index_store.index_cache import (
    compute_album_path,
    load_incremental_index_cache,
)


def test_compute_album_path_handles_library_relative_paths(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trips" / "2024"
    album_root.mkdir(parents=True)

    assert compute_album_path(album_root, library_root) == "Trips/2024"
    assert compute_album_path(library_root, library_root) is None
    assert compute_album_path(tmp_path / "Elsewhere", library_root) is None


def test_load_incremental_index_cache_strips_album_prefix(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Album"
    album_root.mkdir(parents=True)
    reset_global_repository()
    try:
        store = get_global_repository(library_root)
        store.write_rows(
            [
                {
                    "rel": "Album/photo.jpg",
                    "gps": {"lat": 1.0, "lon": 2.0},
                    "mime": "image/jpeg",
                    "id": "asset-1",
                }
            ]
        )

        cache = load_incremental_index_cache(album_root, library_root=library_root)

        assert "photo.jpg" in cache
        assert cache["photo.jpg"]["rel"] == "Album/photo.jpg"
    finally:
        reset_global_repository()
