
from src.iPhoto.library.manager import LibraryManager

def test_get_live_scan_results_scanning_child_viewing_parent(tmp_path):
    """
    Scenario: Scanning a child album (e.g. /Photos/Vacation)
    Viewing: Parent (e.g. /Photos)
    Expected: Items should be prefixed with the child folder name (e.g. Vacation/img.jpg)
    """
    root = tmp_path / "Library"
    child = root / "Vacation"
    child.mkdir(parents=True)

    manager = LibraryManager()

    # Manually setup the scan state
    # Scanning inside "Vacation"
    manager._live_scan_root = child

    # Buffer contains item relative to the scan root (Vacation)
    # So "photo.jpg" here means "/Photos/Vacation/photo.jpg"
    manager._live_scan_buffer = [{"rel": "photo.jpg", "id": "1"}]

    # We are asking for results relative to "root" (/Photos)
    results = manager.get_live_scan_results(relative_to=root)

    assert len(results) == 1
    # The result 'rel' should be relative to 'root'
    # So it should be "Vacation/photo.jpg"
    assert results[0]["rel"] == "Vacation/photo.jpg"

def test_get_live_scan_results_scanning_parent_viewing_child(tmp_path):
    """
    Scenario: Scanning the root album (e.g. /Photos)
    Viewing: Child album (e.g. /Photos/Vacation)
    Expected:
    1. Items not in child should be filtered out.
    2. Items in child should have the child prefix stripped (relative to child).
    """
    root = tmp_path / "Library"
    child = root / "Vacation"
    child.mkdir(parents=True)
    other = root / "Other"
    other.mkdir(parents=True)

    manager = LibraryManager()

    # Scanning "root"
    manager._live_scan_root = root

    # Buffer contains items relative to "root"
    manager._live_scan_buffer = [
        {"rel": "Vacation/photo.jpg", "id": "1"}, # Should be included
        {"rel": "Other/photo.jpg", "id": "2"},    # Should be excluded
        {"rel": "root_photo.jpg", "id": "3"}      # Should be excluded
    ]

    # We are asking for results relative to "child" (/Photos/Vacation)
    results = manager.get_live_scan_results(relative_to=child)

    assert len(results) == 1
    # The result 'rel' should be relative to 'child'
    # So "Vacation/photo.jpg" -> "photo.jpg"
    assert results[0]["rel"] == "photo.jpg"
    assert results[0]["id"] == "1"

def test_get_live_scan_results_same_path(tmp_path):
    """
    Scenario: Scanning and viewing same path
    Expected: No change to 'rel'
    """
    root = tmp_path / "Library"
    root.mkdir()

    manager = LibraryManager()
    manager._live_scan_root = root
    manager._live_scan_buffer = [{"rel": "photo.jpg", "id": "1"}]

    results = manager.get_live_scan_results(relative_to=root)

    assert len(results) == 1
    assert results[0]["rel"] == "photo.jpg"

def test_get_live_scan_results_disjoint_paths(tmp_path):
    """
    Scenario: Scanning /Photos/A, Viewing /Photos/B
    Expected: Empty results
    """
    root = tmp_path / "Library"
    a = root / "A"
    b = root / "B"
    a.mkdir(parents=True)
    b.mkdir(parents=True)

    manager = LibraryManager()
    manager._live_scan_root = a
    manager._live_scan_buffer = [{"rel": "photo.jpg"}]

    results = manager.get_live_scan_results(relative_to=b)

    assert len(results) == 0
