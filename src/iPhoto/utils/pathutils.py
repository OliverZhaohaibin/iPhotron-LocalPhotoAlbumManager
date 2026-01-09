"""Utilities for working with filesystem paths inside iPhoto."""

from __future__ import annotations

import fnmatch
import functools
import os
import re
from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple


def _expand(pattern: str) -> Iterator[str]:
    match = re.search(r"\{([^{}]*,[^{}]*)\}", pattern)
    if not match:
        yield pattern
        return
    prefix = pattern[: match.start()]
    suffix = pattern[match.end() :]
    for option in match.group(1).split(","):
        yield from _expand(prefix + option + suffix)


@functools.lru_cache(maxsize=128)
def _expand_cached(pattern: str) -> Tuple[str, ...]:
    """Return a tuple of expanded patterns from *pattern* with caching.

    This wraps :func:`_expand` to allow caching of the expansion results,
    which is beneficial when the same patterns are checked against many files.
    """
    return tuple(_expand(pattern))


def is_excluded(path: Path, globs: Iterable[str], *, root: Path) -> bool:
    """Return ``True`` if *path* should be excluded based on *globs*.

    The function works on relative POSIX-style paths to provide consistent
    behaviour across operating systems.
    """

    rel = path.relative_to(root).as_posix()
    for pattern in globs:
        for expanded in _expand_cached(pattern):
            if fnmatch.fnmatch(rel, expanded):
                return True
            if expanded.startswith("**/") and fnmatch.fnmatch(rel, expanded[3:]):
                return True
    return False


def should_include(path: Path, include_globs: Iterable[str], exclude_globs: Iterable[str], *, root: Path) -> bool:
    """Return ``True`` if *path* should be scanned."""

    if is_excluded(path, exclude_globs, root=root):
        return False
    rel = path.relative_to(root).as_posix()
    for pattern in include_globs:
        for expanded in _expand_cached(pattern):
            if fnmatch.fnmatch(rel, expanded):
                return True
            if expanded.startswith("**/") and fnmatch.fnmatch(rel, expanded[3:]):
                return True
    return False


def ensure_work_dir(root: Path, name: str = ".iPhoto") -> Path:
    """Ensure that the album work directory exists and return it."""

    work_dir = root / name
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def normalise_for_compare(path: Path) -> Path:
    """Return a normalised ``Path`` suitable for cross-platform comparisons.

    ``Path.resolve`` is insufficient on its own because it preserves the
    original casing on case-insensitive filesystems.  Combining
    :func:`os.path.realpath` with :func:`os.path.normcase` yields a canonical
    representation that collapses symbolic links and performs the necessary
    case folding so that two references to the same directory compare equal
    regardless of how they were produced.
    """

    try:
        resolved = os.path.realpath(path)
    except OSError:
        resolved = str(path)
    return Path(os.path.normcase(resolved))


def is_descendant_path(path: Path, candidate_root: Path) -> bool:
    """Return ``True`` when *path* is located under *candidate_root*.

    The helper treats equality as a positive match so callers can avoid
    special casing.  ``Path.parents`` yields every ancestor of *path*, making
    it a convenient way to check the relationship without manual string
    operations that could break across platforms.
    """

    if path == candidate_root:
        return True

    return candidate_root in path.parents


def normalise_rel_value(value: object) -> Optional[str]:
    """Return a POSIX-formatted relative path for *value* when possible.

    Raises:
        TypeError: If *value* is truthy but not a str or Path.
    """

    if not value:
        return None

    if isinstance(value, (str, Path)):
        return Path(str(value)).as_posix()

    raise TypeError(f"Expected str or Path, got {type(value).__name__}")
