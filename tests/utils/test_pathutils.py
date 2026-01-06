
from src.iPhoto.utils.pathutils import is_excluded, should_include, _expand_cached, _expand

def test_expand_simple():
    pattern = "foo"
    expanded = list(_expand(pattern))
    assert expanded == ["foo"]

def test_expand_braces():
    pattern = "foo.{jpg,png}"
    expanded = sorted(list(_expand(pattern)))
    assert expanded == ["foo.jpg", "foo.png"]

def test_expand_nested_braces():
    pattern = "foo.{a,b}.{1,2}"
    expanded = sorted(list(_expand(pattern)))
    assert expanded == ["foo.a.1", "foo.a.2", "foo.b.1", "foo.b.2"]

def test_expand_cached():
    # Verify cached version behaves same as original logic
    pattern = "test_{a,b}"
    expanded_cached = _expand_cached(pattern)
    assert isinstance(expanded_cached, tuple)
    assert sorted(expanded_cached) == ["test_a", "test_b"]

    # Check cache works (indirectly by calling again)
    expanded_cached_2 = _expand_cached(pattern)
    assert expanded_cached_2 is expanded_cached

def test_is_excluded(tmp_path):
    root = tmp_path

    globs = ["*.ignore", "temp/**"]

    assert is_excluded(root / "file.ignore", globs, root=root)
    assert is_excluded(root / "temp/file.txt", globs, root=root)
    assert not is_excluded(root / "file.txt", globs, root=root)

def test_is_excluded_with_expansion(tmp_path):
    root = tmp_path
    globs = ["*.{ignore,tmp}"]

    assert is_excluded(root / "file.ignore", globs, root=root)
    assert is_excluded(root / "file.tmp", globs, root=root)
    assert not is_excluded(root / "file.txt", globs, root=root)

def test_should_include(tmp_path):
    root = tmp_path
    include = ["*.{jpg,png}"]
    exclude = ["bad.jpg"]

    # Included
    assert should_include(root / "good.jpg", include, exclude, root=root)
    assert should_include(root / "good.png", include, exclude, root=root)

    # Not included
    assert not should_include(root / "good.txt", include, exclude, root=root)

    # Excluded
    assert not should_include(root / "bad.jpg", include, exclude, root=root)
