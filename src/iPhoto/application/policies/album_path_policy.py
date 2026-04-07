"""Album path policy.

Owns all rules for computing library-relative album paths,
stripping/adding album-path prefixes on row ``rel`` fields,
and determining whether a path falls within a sub-album scope.

This consolidates path-transformation logic that was previously
scattered across ``path_normalizer.py``, ``rescan_album_use_case.py``
and ``pair_live_photos_use_case_v2.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from ...utils.logging import get_logger

LOGGER = get_logger()


class AlbumPathPolicy:
    """Compute and transform album-relative and library-relative paths."""

    # ------------------------------------------------------------------
    # Core path computation
    # ------------------------------------------------------------------

    def compute_album_path(self, root: Path, library_root: Optional[Path]) -> Optional[str]:
        """Return library-relative album path when *root* is inside *library_root*.

        Uses ``os.path.relpath`` to tolerate case differences and symlinks.
        Returns ``None`` when outside the library or when *root* points directly
        at the library root.
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
            "AlbumPathPolicy.compute_album_path: root=%s, library_root=%s, rel=%s",
            root,
            library_root,
            rel,
        )
        return rel

    # ------------------------------------------------------------------
    # Row rel-path prefix helpers
    # ------------------------------------------------------------------

    def prefix_rows(self, rows: List[dict], album_path: str) -> List[dict]:
        """Rewrite each row's ``rel`` field to be library-scoped.

        Prepends ``<album_path>/`` to every ``rel`` value that does not already
        contain the prefix.  This is needed when scan rows are produced using
        album-relative paths but must be stored in the global library index.
        """
        if not album_path:
            return rows
        prefix = album_path + "/"
        result: List[dict] = []
        for row in rows:
            rel = row.get("rel")
            if not isinstance(rel, str):
                result.append(row)
                continue
            if rel.startswith(prefix):
                result.append(row)
            else:
                new_row = dict(row)
                new_row["rel"] = f"{album_path}/{rel}"
                result.append(new_row)
        return result

    def strip_album_prefix(self, rows: List[dict], album_path: str) -> List[dict]:
        """Return rows scoped to *album_path* with matching prefixes removed.

        For rows whose ``rel`` starts with ``<album_path>/``, a copy of the row is
        returned with that prefix stripped from ``rel``. Rows whose ``rel`` does
        not start with the prefix are still kept unchanged when they are already
        leaf-level paths (that is, they contain no ``/``). Unprefixed rows that
        contain ``/`` are excluded from the result.
        """
        if not album_path:
            return rows
        prefix = album_path + "/"
        result: List[dict] = []
        for row in rows:
            rel = row.get("rel", "")
            if rel.startswith(prefix):
                new_row = dict(row)
                new_row["rel"] = rel[len(prefix):]
                result.append(new_row)
            elif "/" not in rel:
                result.append(row)
        return result

    # ------------------------------------------------------------------
    # Scope checks
    # ------------------------------------------------------------------

    def is_within_scope(
        self,
        path: Path,
        album_root: Path,
        *,
        include_subalbums: bool = False,
    ) -> bool:
        """Return ``True`` if *path* is within *album_root*.

        When *include_subalbums* is ``False`` only direct children are considered.
        """
        try:
            rel = path.resolve().relative_to(album_root.resolve())
        except (ValueError, OSError):
            return False
        if not include_subalbums:
            return len(rel.parts) == 1
        return True


__all__ = ["AlbumPathPolicy"]
