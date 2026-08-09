from __future__ import annotations


def test_legacy_album_shim_imports() -> None:
    from iPhoto.legacy.models.album import Album

    assert Album.__name__ == "Album"


def test_legacy_library_manager_shim_imports() -> None:
    from iPhoto.legacy.library.manager import LibraryManager
    from iPhoto.library.runtime_controller import LibraryRuntimeController

    assert LibraryManager is LibraryRuntimeController
