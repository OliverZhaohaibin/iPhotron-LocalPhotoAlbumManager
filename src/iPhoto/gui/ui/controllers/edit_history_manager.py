"""Manages the undo/redo stack for an edit session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from ..models.edit_session import EditSession


class EditHistoryManager(QObject):
    """Encapsulates undo/redo logic for edit parameters."""

    def __init__(
        self,
        session: EditSession | None = None,
        history_limit: int = 50,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._history_limit = history_limit
        self._undo_stack: list[dict[str, float | bool]] = []
        self._redo_stack: list[dict[str, float | bool]] = []

    def set_session(self, session: EditSession | None) -> None:
        """Bind to a new edit session and clear history."""
        self._session = session
        self.clear()

    def clear(self) -> None:
        """Clear both undo and redo stacks."""
        self._undo_stack.clear()
        self._redo_stack.clear()

    def push_undo_state(self) -> None:
        """Capture the current session state onto the undo stack."""
        if self._session is None:
            return

        current_state = self._session.values()
        self._undo_stack.append(current_state)
        if len(self._undo_stack) > self._history_limit:
            self._undo_stack.pop(0)

        # New action clears the redo history.
        self._redo_stack.clear()

    def undo(self) -> None:
        """Revert the last edit action."""
        if self._session is None or not self._undo_stack:
            return

        current_state = self._session.values()
        self._redo_stack.append(current_state)

        previous_state = self._undo_stack.pop()
        # Updating the session triggers the UI refresh automatically via signals.
        self._session.set_values(previous_state)

    def redo(self) -> None:
        """Restore the previously undone edit action."""
        if self._session is None or not self._redo_stack:
            return

        current_state = self._session.values()
        self._undo_stack.append(current_state)
        if len(self._undo_stack) > self._history_limit:
            self._undo_stack.pop(0)

        next_state = self._redo_stack.pop()
        self._session.set_values(next_state)
