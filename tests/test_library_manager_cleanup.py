from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for library manager tests")
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available for tests")

from PySide6.QtWidgets import QApplication

from iPhoto.config import RECENTLY_DELETED_DIR_NAME
from iPhoto.cache.index_store import IndexStore
from iPhoto.library.manager import LibraryManager


@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _write_index_rows(store: IndexStore, rels: list[str]) -> None:
    """Populate index rows for the provided rel paths."""

    rows = [{"rel": rel, "id": f"id:{rel}"} for rel in rels]
    store.write_rows(rows)


def test_cleanup_deleted_index_removes_missing_rows(tmp_path: Path, qapp: QApplication) -> None:
    library_root = tmp_path / "Library"
    library_root.mkdir()

    manager = LibraryManager()
    manager.bind_path(library_root)
    trash_root = manager.ensure_deleted_directory()

    keep = trash_root / "keep.jpg"
    keep.write_bytes(b"data")

    missing = f"{RECENTLY_DELETED_DIR_NAME}/missing.jpg"
    present = f"{RECENTLY_DELETED_DIR_NAME}/{keep.name}"
    store = IndexStore(library_root)
    _write_index_rows(store, [present, missing])

    removed = manager.cleanup_deleted_index()
    assert removed == 1

    remaining = list(store.read_all())
    assert [row.get("rel") for row in remaining] == [present]

    keep.unlink()
    removed_again = manager.cleanup_deleted_index()
    assert removed_again == 1
    assert list(store.read_all()) == []
