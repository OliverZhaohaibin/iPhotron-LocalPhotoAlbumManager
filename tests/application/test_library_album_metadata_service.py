from __future__ import annotations

from pathlib import Path

from iPhoto.bootstrap.library_album_metadata_service import LibraryAlbumMetadataService
from iPhoto.infrastructure.repositories.album_manifest_repository import (
    AlbumManifestRepository,
)


class _StateRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def set_favorite_status(self, rel: str, is_favorite: bool) -> None:
        self.calls.append((rel, is_favorite))


def test_set_cover_persists_manifest(tmp_path: Path) -> None:
    album_root = tmp_path / "Album"
    album_root.mkdir()

    service = LibraryAlbumMetadataService(album_root)
    service.set_cover(album_root, "cover.jpg")

    manifest = AlbumManifestRepository().load_manifest(album_root)
    assert manifest["cover"] == "cover.jpg"


def test_toggle_featured_updates_current_and_library_album(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Trip"
    album_root.mkdir(parents=True)
    (album_root / "photo.jpg").touch()

    root_state = _StateRepository()
    service = LibraryAlbumMetadataService(
        library_root,
        state_repository=root_state,
    )

    result = service.toggle_featured(album_root, "photo.jpg")
    assert result.is_featured is True
    assert result.errors == []

    repository = AlbumManifestRepository()
    album_manifest = repository.load_manifest(album_root)
    root_manifest = repository.load_manifest(library_root)
    assert album_manifest["featured"] == ["photo.jpg"]
    assert root_manifest["featured"] == ["Trip/photo.jpg"]
    assert root_state.calls == [
        ("Trip/photo.jpg", True),
        ("Trip/photo.jpg", True),
    ]


def test_toggle_featured_from_library_root_for_root_asset(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()
    (library_root / "photo.jpg").touch()

    root_state = _StateRepository()
    service = LibraryAlbumMetadataService(
        library_root,
        state_repository=root_state,
    )

    result = service.toggle_featured(library_root, "photo.jpg")
    assert result.is_featured is True
    assert result.errors == []

    manifest = AlbumManifestRepository().load_manifest(library_root)
    assert manifest["featured"] == ["photo.jpg"]
    assert root_state.calls == [("photo.jpg", True)]


def test_toggle_featured_outside_bound_library_uses_fallback_state_repository(
    tmp_path: Path,
) -> None:
    library_root = tmp_path / "Library"
    album_root = tmp_path / "Standalone"
    library_root.mkdir()
    album_root.mkdir()
    (album_root / "photo.jpg").touch()

    root_state = _StateRepository()
    fallback_repo = _StateRepository()
    created_roots: list[Path] = []

    def state_repository_factory(root: Path) -> _StateRepository:
        created_roots.append(Path(root))
        return fallback_repo

    service = LibraryAlbumMetadataService(
        library_root,
        state_repository=root_state,
        state_repository_factory=state_repository_factory,
    )

    result = service.toggle_featured(album_root, "photo.jpg")
    assert result.is_featured is True
    assert result.errors == []

    manifest = AlbumManifestRepository().load_manifest(album_root)
    assert manifest["featured"] == ["photo.jpg"]
    assert created_roots == [album_root]
    assert fallback_repo.calls == [("photo.jpg", True)]
    assert root_state.calls == []


def test_ensure_featured_entries_updates_scoped_manifest(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    album_root = library_root / "Hike"
    album_root.mkdir(parents=True)
    imported = [album_root / "a.jpg", album_root / "nested" / "b.jpg", library_root / "skip.txt"]

    service = LibraryAlbumMetadataService(library_root)
    service.ensure_featured_entries(album_root, imported)

    manifest = AlbumManifestRepository().load_manifest(album_root)
    assert sorted(manifest["featured"]) == ["a.jpg", "nested/b.jpg"]


def test_toggle_featured_identifies_correct_physical_root_nested(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    events_root = library_root / "Events"
    sub_dir = events_root / "2023"
    sub_dir.mkdir(parents=True)
    (sub_dir / "photo.jpg").touch()

    repository = AlbumManifestRepository()
    repository.load_manifest(library_root)
    repository.load_manifest(events_root)
    root_state = _StateRepository()
    service = LibraryAlbumMetadataService(
        library_root,
        state_repository=root_state,
    )

    ref = "Events/2023/photo.jpg"
    result = service.toggle_featured(library_root, ref)
    assert result.is_featured is True
    assert result.errors == []

    root_manifest = repository.load_manifest(library_root)
    events_manifest = repository.load_manifest(events_root)
    assert root_manifest["featured"] == [ref]
    assert events_manifest["featured"] == ["2023/photo.jpg"]
    assert not (sub_dir / ".iphoto.album.json").exists()


def test_toggle_featured_skips_missing_ancestor_directories(tmp_path: Path) -> None:
    library_root = tmp_path / "Library"
    events_root = library_root / "Events"
    events_root.mkdir(parents=True)

    repository = AlbumManifestRepository()
    repository.load_manifest(library_root)
    repository.load_manifest(events_root)

    root_state = _StateRepository()
    service = LibraryAlbumMetadataService(
        library_root,
        state_repository=root_state,
    )

    ref = "Events/2023/Trip/photo.jpg"
    result = service.toggle_featured(library_root, ref)
    assert result.is_featured is True
    assert result.errors == []

    root_manifest = repository.load_manifest(library_root)
    events_manifest = repository.load_manifest(events_root)
    assert root_manifest["featured"] == [ref]
    assert events_manifest["featured"] == ["2023/Trip/photo.jpg"]
