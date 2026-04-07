"""Move aftercare service.

Owns the business rules for computing the side-effects of a completed move
operation: which index rows were removed/added and which album roots need
an index/links refresh.

Previously these computations were performed inline inside
``gui/services/library_update_service.py``.  Moving them to the application
layer makes the rules testable without Qt and enforces the principle that
presentation services should only perform Qt coordination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ...utils.logging import get_logger

LOGGER = get_logger()


@dataclass
class MoveAftercareResult:
    """Consolidated result of the post-move bookkeeping computations.

    Consumers (Qt adapters) use this to emit signals and schedule additional
    work without needing to understand the underlying business rules.
    """

    removed_rels: list[str] = field(default_factory=list)
    added_rels: list[str] = field(default_factory=list)
    # ``{path_key: (path, should_restart)}`` – same format as
    # :meth:`MoveBookkeepingService.compute_refresh_targets`.
    refresh_targets: dict[str, tuple[Path, bool]] = field(default_factory=dict)


class MoveAftercareService:
    """Orchestrate post-move bookkeeping computations.

    This service is a thin coordinator over
    :class:`~iPhoto.application.services.move_bookkeeping_service.MoveBookkeepingService`.
    It provides a single high-level entry point so presentation code does not
    need to call the lower-level service methods directly.

    All methods are pure-Python and Qt-free.
    """

    def __init__(self) -> None:
        from .move_bookkeeping_service import MoveBookkeepingService

        self._bookkeeping = MoveBookkeepingService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_aftermath(
        self,
        moved_pairs: list[tuple[Path, Path]],
        source_root: Path,
        destination_root: Path,
        current_root: Path | None,
        library_root: Path | None,
        *,
        source_ok: bool,
        destination_ok: bool,
    ) -> MoveAftercareResult:
        """Compute all side-effects of a completed move operation.

        Parameters
        ----------
        moved_pairs:
            ``[(original_path, target_path), ...]`` pairs from the move worker.
        source_root:
            Album root the assets were moved *from*.
        destination_root:
            Album root the assets were moved *to*.
        current_root:
            The album root currently open in the UI (may be ``None``).
        library_root:
            The library root (may be ``None`` for standalone albums).
        source_ok:
            Whether the source album was successfully modified.
        destination_ok:
            Whether the destination album was successfully modified.
        """

        removed_rels, added_rels = self._bookkeeping.compute_move_rels(
            moved_pairs, library_root, source_root, destination_root
        )

        refresh_targets = self._bookkeeping.compute_refresh_targets(
            moved_pairs,
            source_root,
            destination_root,
            current_root,
            library_root,
            source_ok=source_ok,
            destination_ok=destination_ok,
        )

        return MoveAftercareResult(
            removed_rels=removed_rels,
            added_rels=added_rels,
            refresh_targets=refresh_targets,
        )

    def consume_forced_reload(self, path: Path) -> bool:
        """Return ``True`` and consume the stale marker when *path* is stale."""

        return self._bookkeeping.consume_forced_reload(path)

    def reset(self) -> None:
        """Drop all stale markers and resolution caches."""

        self._bookkeeping.reset()


__all__ = ["MoveAftercareResult", "MoveAftercareService"]
