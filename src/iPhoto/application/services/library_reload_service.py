"""Library reload service.

Owns the business rules that decide *which* UI reload action should be
performed after a library change (scan completion, asset restore, etc.).

Previously this decision logic was scattered inside the presentation layer
(``gui/services/library_update_service.py``).  Extracting it here keeps the
presentation layer as a thin Qt coordinator and makes the reload decisions
unit-testable without a running Qt application.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...utils.logging import get_logger

LOGGER = get_logger()


@dataclass(frozen=True)
class ReloadAction:
    """Describes what UI reload action is required after a library change.

    Attributes
    ----------
    should_reload_current:
        The view that is currently showing *root* should reload.
    should_reload_as_library:
        The library-level view (showing *root* as a parent) should reload.
    target_root:
        The root path that the UI should reload.  ``None`` means no action.
    """

    should_reload_current: bool = False
    should_reload_as_library: bool = False
    target_root: Path | None = None

    @property
    def requires_action(self) -> bool:
        """Return ``True`` when at least one reload is needed."""

        return self.should_reload_current or self.should_reload_as_library


class LibraryReloadService:
    """Compute UI reload actions after library changes.

    All methods are pure-Python and Qt-free so they can be unit-tested without
    a running Qt application.

    The service delegates actual reload-decision rules to
    :class:`~iPhoto.application.services.trash_service.TrashService`,
    providing a single high-level entry point for presentation code.
    """

    def __init__(self) -> None:
        from .trash_service import TrashService

        self._trash_service = TrashService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_restore_reload_action(
        self,
        restored_path: Path,
        current_root: Path | None,
        library_root: Path | None,
    ) -> ReloadAction:
        """Return the reload action needed after a restore rescan completes.

        Parameters
        ----------
        restored_path:
            The album root that was rescanned after a restore operation.
        current_root:
            The album root currently open in the UI.
        library_root:
            The library root (may be ``None``).
        """

        should_reload, should_reload_as_lib, _ = self._trash_service.compute_restore_reload_action(
            restored_path, current_root, library_root
        )

        if not should_reload and not should_reload_as_lib:
            return ReloadAction()

        return ReloadAction(
            should_reload_current=should_reload,
            should_reload_as_library=should_reload_as_lib,
            target_root=current_root,
        )

    def compute_scan_reload_action(
        self,
        scanned_root: Path,
        current_root: Path | None,
        *,
        model_loading_due_to_scan: bool = False,
    ) -> ReloadAction:
        """Return the reload action needed after a background scan completes.

        Parameters
        ----------
        scanned_root:
            The album root that was just scanned.
        current_root:
            The album root currently open in the UI.
        model_loading_due_to_scan:
            When ``True`` the view model is already being loaded as a direct
            consequence of this scan; emitting an additional reload would cause
            a duplicate refresh.
        """

        if model_loading_due_to_scan:
            return ReloadAction()

        return ReloadAction(
            should_reload_current=True,
            target_root=scanned_root,
        )


__all__ = ["LibraryReloadService", "ReloadAction"]
