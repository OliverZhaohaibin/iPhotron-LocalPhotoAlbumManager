from pathlib import Path

import pytest

try:
    from src.iPhoto.gui.ui.tasks.asset_loader_worker import compute_album_path
except Exception as exc:  # pragma: no cover - environment missing Qt deps
    pytest.skip(f"PySide6 not available: {exc}", allow_module_level=True)


def test_compute_album_path_outside_library_uses_library_root(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = tmp_path / "ExternalAlbum"
    library_root.mkdir()
    album_root.mkdir()

    index_root, album_path = compute_album_path(album_root, library_root)

    assert index_root == album_root
    assert album_path is None


def test_compute_album_path_relative_inside_library(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    library_root.mkdir()
    album_root.mkdir()

    index_root, album_path = compute_album_path(album_root, library_root)

    assert index_root == library_root
    assert album_path == "Trip"


def test_compute_album_path_nested_inside_library(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trips" / "2024"
    album_root.mkdir(parents=True)

    index_root, album_path = compute_album_path(album_root, library_root)

    assert index_root == library_root
    assert album_path == "Trips/2024"
