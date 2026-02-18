"""Path normalisation helpers used by the application layer."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .utils.logging import get_logger

LOGGER = get_logger()


def compute_album_path(root: Path, library_root: Optional[Path]) -> Optional[str]:
    """Return library-relative album path when root is inside library_root.

    Uses os.path.relpath to tolerate case differences and symlinks. Returns
    None when outside the library or when pointing at the library root.
    """
    if not library_root:
        return None
    try:
        rel = Path(os.path.relpath(root, library_root)).as_posix()
    except (ValueError, OSError):
        return None

    if rel.startswith(".."):
        return None
    if rel in (".", ""):
        return None
    # Debug trace to help diagnose album filtering issues
    LOGGER.debug(
        "Computed album path: root=%s, library_root=%s, rel=%s",
        root,
        library_root,
        rel,
    )
    return rel


def normalise_rel_key(rel_value: object) -> Optional[str]:
    """Return a POSIX-formatted representation of *rel_value* when possible.

    The index uses the relative path under the album root as its stable
    identifier.  Callers occasionally pass :class:`pathlib.Path` instances while
    other code paths hand over plain strings.  Normalising via
    :meth:`Path.as_posix` collapses both representations into a single canonical
    form so lookups remain stable regardless of the originating caller or the
    underlying operating system.
    """

    if isinstance(rel_value, str) and rel_value:
        return Path(rel_value).as_posix()
    if isinstance(rel_value, Path):
        return rel_value.as_posix()
    if rel_value:
        return Path(str(rel_value)).as_posix()
    return None


__all__ = ["compute_album_path", "normalise_rel_key"]
