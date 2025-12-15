from pathlib import Path

from src.iPhoto.library.manager import LibraryManager


def test_paths_are_siblings_basic():
    manager = LibraryManager()
    p1 = Path("/tmp/library/a")
    p2 = Path("/tmp/library/b")
    assert manager._paths_are_siblings(p1, p2)


def test_paths_are_siblings_reject_parent_child_and_root():
    manager = LibraryManager()
    parent = Path("/tmp/library")
    child = parent / "child"
    assert not manager._paths_are_siblings(parent, child)
    assert not manager._paths_are_siblings(Path("/"), Path("/tmp"))
    assert not manager._paths_are_siblings(Path("/tmp/a"), Path("/other/b"))
