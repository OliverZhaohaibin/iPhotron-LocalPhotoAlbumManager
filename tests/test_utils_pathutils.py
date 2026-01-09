"""Tests for pathutils."""

import pytest
from pathlib import Path
from src.iPhoto.utils.pathutils import (
    _expand,
    is_excluded,
    normalise_for_compare,
    is_descendant_path,
    normalise_rel_value,
)

def test_expand_single_brace():
    """Verify that single braces without commas are NOT expanded."""
    # Bug: {a} expanded to a
    # Fix: {a} stays {a}
    assert list(_expand("{a}")) == ["{a}"]

def test_expand_with_commas():
    """Verify that braces with commas ARE expanded."""
    assert sorted(list(_expand("{a,b}"))) == ["a", "b"]
    assert sorted(list(_expand("file_{1,2}.txt"))) == ["file_1.txt", "file_2.txt"]

def test_expand_nested():
    """Verify nested brace expansion."""
    # {{a,b},c} -> {a,c}, {b,c} -> a, c, b, c
    # Note: Output order depends on implementation, so we sort.
    # We expect duplicates because expansion logic yields them.
    assert sorted(list(_expand("{{a,b},c}"))) == sorted(["a", "c", "b", "c"])

def test_no_expand_literals():
    """Verify that filenames with braces can be matched if pattern has braces."""
    # If I have a file "file_{1}.txt" and pattern "file_{1}.txt"
    # It should NOT expand pattern to "file_1.txt"
    assert list(_expand("file_{1}.txt")) == ["file_{1}.txt"]

def test_is_excluded_with_braces(tmp_path):
    """Verify is_excluded handles files with braces correctly."""
    root = tmp_path
    path = root / "file_{1}.txt"

    # With FIX:
    # _expand("file_{1}.txt") -> "file_{1}.txt"
    # fnmatch("file_{1}.txt", "file_{1}.txt") -> True
    assert is_excluded(path, ["file_{1}.txt"], root=root) is True

def test_normalise_for_compare(tmp_path):
    """Verify normalise_for_compare handles case sensitivity and resolution."""
    # Create a real file to resolve
    (tmp_path / "TestDir").mkdir()
    real_path = tmp_path / "TestDir"

    # On Linux (case sensitive), casing matters for resolving but normcase usually lowercases or leaves as is depending on OS.
    # We can check that it returns a Path and normcase is applied.

    normalised = normalise_for_compare(real_path)
    assert isinstance(normalised, Path)
    # Check that it's absolute
    assert normalised.is_absolute()

    # Check idempotency
    assert normalise_for_compare(normalised) == normalised

def test_is_descendant_path(tmp_path):
    """Verify is_descendant_path logic."""
    root = tmp_path
    child = root / "child"
    grandchild = child / "grandchild"
    sibling = tmp_path / "sibling"

    assert is_descendant_path(child, root)
    assert is_descendant_path(grandchild, root)
    assert is_descendant_path(root, root)  # Equality is treated as positive match
    assert not is_descendant_path(root, child)
    assert not is_descendant_path(sibling, child)

def test_normalise_rel_value():
    """Verify normalise_rel_value conversion and type safety."""
    assert normalise_rel_value("foo/bar") == "foo/bar"
    assert normalise_rel_value(Path("foo/bar")) == "foo/bar"
    assert normalise_rel_value(None) is None
    assert normalise_rel_value("") is None

    # Ensure forward slashes on Windows too
    p = Path("foo") / "bar"
    assert normalise_rel_value(p) == "foo/bar"

    # Test invalid types raise TypeError
    with pytest.raises(TypeError):
        normalise_rel_value(123)

    with pytest.raises(TypeError):
        normalise_rel_value(["list"])
