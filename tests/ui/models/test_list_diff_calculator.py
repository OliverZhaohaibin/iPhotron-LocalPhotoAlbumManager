"""Tests for ListDiffCalculator."""

from src.iPhoto.gui.ui.models.list_diff_calculator import ListDiffCalculator

def test_diff_empty_to_empty():
    current = []
    fresh = []
    diff = ListDiffCalculator.calculate_diff(current, fresh)
    assert diff.is_empty_to_empty
    assert not diff.is_reset
    assert not diff.structure_changed

def test_diff_reset_from_empty():
    current = []
    fresh = [{"rel": "a"}]
    diff = ListDiffCalculator.calculate_diff(current, fresh)
    assert diff.is_reset
    assert not diff.is_empty_to_empty

def test_diff_removals():
    current = [{"rel": "a"}, {"rel": "b"}, {"rel": "c"}]
    fresh = [{"rel": "a"}, {"rel": "c"}]
    diff = ListDiffCalculator.calculate_diff(current, fresh)

    assert diff.removed_indices == [1] # 'b' is at index 1
    assert not diff.inserted_items
    assert diff.structure_changed

def test_diff_insertions():
    current = [{"rel": "a"}, {"rel": "c"}]
    fresh = [{"rel": "a"}, {"rel": "b"}, {"rel": "c"}]
    diff = ListDiffCalculator.calculate_diff(current, fresh)

    assert not diff.removed_indices
    # 'b' should be inserted at index 1
    assert len(diff.inserted_items) == 1
    index, item, key = diff.inserted_items[0]
    assert index == 1
    assert item["rel"] == "b"
    assert diff.structure_changed

def test_diff_updates():
    current = [{"rel": "a", "val": 1}]
    fresh = [{"rel": "a", "val": 2}]
    diff = ListDiffCalculator.calculate_diff(current, fresh)

    assert not diff.removed_indices
    assert not diff.inserted_items
    assert not diff.structure_changed

    # Logic changed to return ITEMS, not INDICES
    assert len(diff.changed_items) == 1
    assert diff.changed_items[0]["val"] == 2

def test_diff_mixed():
    # Start: [A, B, C]
    # End:   [A, C', D] (B removed, C updated, D inserted)
    current = [
        {"rel": "a", "val": 1},
        {"rel": "b", "val": 1},
        {"rel": "c", "val": 1},
    ]
    fresh = [
        {"rel": "a", "val": 1},
        {"rel": "c", "val": 2},
        {"rel": "d", "val": 1},
    ]
    diff = ListDiffCalculator.calculate_diff(current, fresh)

    # Removals: B (index 1)
    assert diff.removed_indices == [1]

    # Insertions: D (index 2 in fresh list)
    assert len(diff.inserted_items) == 1
    assert diff.inserted_items[0][0] == 2
    assert diff.inserted_items[0][1]["rel"] == "d"

    # Updates: C
    assert len(diff.changed_items) == 1
    assert diff.changed_items[0]["rel"] == "c"
    assert diff.changed_items[0]["val"] == 2
