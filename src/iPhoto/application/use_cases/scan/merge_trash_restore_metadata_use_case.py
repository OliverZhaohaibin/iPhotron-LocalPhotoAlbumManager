"""Merge trash restore metadata use case.

When the "Recently Deleted" album is rescanned, restore-metadata fields
(``original_rel_path``, ``original_album_id``, ``original_album_subpath``)
must be carried forward from the previous index snapshot so that the
quick-restore workflow continues to work correctly.

This use case delegates the business rules to
:class:`~iPhoto.application.policies.trash_restore_policy.TrashRestorePolicy`
and provides a single call-site for both the synchronous and asynchronous
scan paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from ....config import RECENTLY_DELETED_DIR_NAME
from ....utils.logging import get_logger
from ...policies.trash_restore_policy import TrashRestorePolicy

LOGGER = get_logger()


class MergeTrashRestoreMetadataUseCase:
    """Merge restore-metadata fields into freshly scanned trash rows."""

    def __init__(self, policy: Optional[TrashRestorePolicy] = None) -> None:
        self._policy = policy or TrashRestorePolicy()

    def execute(
        self,
        rows: List[dict],
        album_root: Path,
        library_root: Optional[Path] = None,
    ) -> List[dict]:
        """Merge restore metadata into *rows* when *album_root* is the trash folder.

        Returns *rows* unchanged when *album_root* is not the recently-deleted
        directory.  When it is the trash, any row matching a previous index entry
        that carries restore-metadata fields will have those fields populated.
        """
        if album_root.name != RECENTLY_DELETED_DIR_NAME:
            return rows

        from ....cache.index_store import get_global_repository

        db_root = library_root if library_root else album_root
        store = get_global_repository(db_root)

        album_path, allow_read_all = self._policy.resolve_trash_album_path(
            album_root, library_root
        )
        preserved = self._policy.collect_preserved_rows(
            store,
            album_path=album_path,
            allow_read_all=allow_read_all,
        )

        return self._policy.merge_preserved_metadata(rows, preserved)


__all__ = ["MergeTrashRestoreMetadataUseCase"]
