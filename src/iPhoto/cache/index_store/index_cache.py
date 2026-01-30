"""Utilities for resolving album paths and loading incremental index caches."""

from __future__ import annotations

from pathlib import Path
import os
from typing import Dict, Optional

from ...errors import IndexCorruptedError
from ...utils.logging import get_logger
from .repository import get_global_repository

LOGGER = get_logger()


def compute_album_path(root: Path, library_root: Optional[Path]) -> Optional[str]:
    """Return library-relative album path when root is inside library_root.

    Uses ``os.path.relpath`` to tolerate case differences and symlinks. Returns
    ``None`` when outside the library or when pointing at the library root.
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
    LOGGER.debug(
        "Computed album path: root=%s, library_root=%s, rel=%s",
        root,
        library_root,
        rel,
    )
    return rel


def normalise_rel_key(rel_value: object) -> Optional[str]:
    """Return a POSIX-formatted representation of *rel_value* when possible."""

    if isinstance(rel_value, str) and rel_value:
        return Path(rel_value).as_posix()
    if isinstance(rel_value, Path):
        return rel_value.as_posix()
    if rel_value:
        return Path(str(rel_value)).as_posix()
    return None


def load_incremental_index_cache(
    root: Path, library_root: Optional[Path] = None
) -> Dict[str, dict]:
    """Load the existing index into a dictionary for incremental scanning.

    Args:
        root: The album root directory.
        library_root: If provided, use this as the database root (global database).
    """
    db_root = library_root if library_root else root
    store = get_global_repository(db_root)
    existing_index: Dict[str, dict] = {}

    album_path = compute_album_path(root, library_root)
    try:
        if album_path:
            rows = store.read_album_assets(album_path, include_subalbums=True)
        else:
            rows = store.read_all()
        album_prefix = f"{album_path}/" if album_path else None
        for row in rows:
            rel_key = normalise_rel_key(row.get("rel"))
            if not rel_key:
                continue
            if album_prefix and rel_key.startswith(album_prefix):
                rel_key = rel_key[len(album_prefix):]
            existing_index[rel_key] = row
    except IndexCorruptedError:
        pass
    return existing_index
