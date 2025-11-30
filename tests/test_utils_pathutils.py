"""Tests for pathutils."""

import pytest
from pathlib import Path
try:
    from iPhoto.utils.pathutils import _expand, is_excluded, should_include
except ImportError:
    from iPhotos.src.iPhoto.utils.pathutils import _expand, is_excluded, should_include

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
