"""Restore aftercare service.

Owns the business rules that determine whether post-restore rescans are needed
and which album roots require them.

Previously these rules were inline inside
``gui/services/library_update_service.py``.  Extracting them here makes the
logic testable without a running Qt application and removes business
decision-making from the presentation layer.
"""

from __future__ import annotations

from pathlib import Path

from ...utils.logging import get_logger

LOGGER = get_logger()


class RestoreAftercareService:
    """Business rules for the post-restore phase of a move operation.

    All methods are pure-Python and Qt-free so they can be unit-tested without
    a running Qt application.
    """

    # ------------------------------------------------------------------
    # Restore rescan eligibility
    # ------------------------------------------------------------------

    def should_trigger_restore_rescan(
        self,
        *,
        is_restore_operation: bool,
        destination_ok: bool,
        source_root: Path,
        trash_root: Path | None,
    ) -> bool:
        """Return ``True`` when a post-restore rescan of the destination is needed.

        Parameters
        ----------
        is_restore_operation:
            Whether the move was a restore-from-trash operation.
        destination_ok:
            Whether the destination album was successfully written to.
        source_root:
            The album root that assets were moved *from* (the trash directory
            in a restore scenario).
        trash_root:
            The library's current trash directory.  ``None`` disables the check
            and causes the method to return ``False``.
        """

        if not is_restore_operation:
            return False
        if not destination_ok:
            return False
        if trash_root is None:
            return False

        return self._paths_equal(source_root, trash_root)

    # ------------------------------------------------------------------
    # Restore rescan target computation
    # ------------------------------------------------------------------

    def compute_restore_rescan_targets(
        self,
        moved_pairs: list[tuple[Path, Path]],
        library_root: Path | None,
    ) -> list[Path]:
        """Return album roots that should be rescanned after a restore operation.

        Delegates to :class:`~iPhoto.application.services.move_bookkeeping_service.MoveBookkeepingService`
        so the caller does not need to know the location details.
        """

        from .move_bookkeeping_service import MoveBookkeepingService

        return MoveBookkeepingService().compute_restore_rescan_targets(moved_pairs, library_root)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _paths_equal(self, a: Path, b: Path) -> bool:
        try:
            return a.resolve() == b.resolve()
        except OSError:
            return a == b


__all__ = ["RestoreAftercareService"]
