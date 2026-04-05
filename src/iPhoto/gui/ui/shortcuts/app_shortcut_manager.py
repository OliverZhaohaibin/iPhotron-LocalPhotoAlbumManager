"""Centralised keyboard shortcut registry for the main application window.

All global (window-level) keyboard shortcuts are **defined and wired in this
single module**.  Adding, modifying, or removing a shortcut requires touching
only this file – not individual coordinators or widgets.

Design principles
-----------------
* Every shortcut uses ``Qt.ShortcutContext.WindowShortcut`` so delivery is
  guaranteed while the application window is active.
* Routing is done at *dispatch time* by inspecting the ``ViewRouter`` state,
  which avoids creating multiple overlapping shortcuts for the same key.
* Focus-sensitive shortcuts (Up / Down for volume) stay in
  ``VideoArea.keyPressEvent`` because making them window-level would
  intercept normal arrow-key navigation in sliders and list views.

Shortcut table (all in one place)
----------------------------------
Key       Context              Action
--------- -------------------- ----------------------------------------
Space     Detail / Edit video  Toggle play / pause
M         Detail / Edit video  Toggle mute
Left      Edit video           Step one frame backward
Right     Edit video           Step one frame forward
.         Any                  Toggle favourite
Escape    App-wide             Exit full-screen
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QLineEdit, QTextEdit

if TYPE_CHECKING:
    from iPhoto.gui.coordinators.edit_coordinator import EditCoordinator
    from iPhoto.gui.coordinators.view_router import ViewRouter
    from iPhoto.gui.ui.widgets.video_area import VideoArea


class AppShortcutManager(QObject):
    """Owns and dispatches all window-level keyboard shortcuts.

    Parameters
    ----------
    window:
        The main application window – parent for all ``QShortcut`` objects.
    router:
        The ``ViewRouter`` used to determine which view is currently active.
    toggle_favorite_cb:
        Callable invoked when the user presses ``.``.
    exit_fullscreen_cb:
        Callable invoked when the user presses ``Escape``.
    parent:
        Optional QObject parent for memory management.
    """

    def __init__(
        self,
        window: QObject,
        router: ViewRouter,
        *,
        toggle_favorite_cb: Callable[[], None],
        exit_fullscreen_cb: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._router = router
        self._toggle_favorite_cb = toggle_favorite_cb
        self._exit_fullscreen_cb = exit_fullscreen_cb

        # Late-bound dependencies (set after construction via setters)
        self._video_area: VideoArea | None = None
        self._edit: EditCoordinator | None = None

        self._shortcuts: list[QShortcut] = []
        self._register_all(window)

    # ------------------------------------------------------------------
    # Dependency injection
    # ------------------------------------------------------------------

    def set_video_area(self, video_area: VideoArea) -> None:
        """Bind the shared ``VideoArea`` instance."""
        self._video_area = video_area

    def set_edit_coordinator(self, edit: EditCoordinator) -> None:
        """Bind the ``EditCoordinator`` for edit-mode transport shortcuts."""
        self._edit = edit

    # ------------------------------------------------------------------
    # Internal registration helpers
    # ------------------------------------------------------------------

    def _add(self, key: Qt.Key | QKeySequence, handler: Callable[[], None]) -> QShortcut:
        seq = QKeySequence(key) if isinstance(key, Qt.Key) else key
        sc = QShortcut(seq, self.parent())
        sc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc.activated.connect(handler)
        self._shortcuts.append(sc)
        return sc

    def _add_app(self, key: Qt.Key | QKeySequence, handler: Callable[[], None]) -> QShortcut:
        seq = QKeySequence(key) if isinstance(key, Qt.Key) else key
        sc = QShortcut(seq, self.parent())
        sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc.activated.connect(handler)
        self._shortcuts.append(sc)
        return sc

    # ------------------------------------------------------------------
    # Shortcut registration — single source of truth
    # ------------------------------------------------------------------

    def _register_all(self, _window: QObject) -> None:  # noqa: ARG002 (window arg reserved)
        # fmt: off
        # ── Playback ──────────────────────────────────────────────────────────
        self._add(Qt.Key.Key_Space,  self._on_play_pause)
        self._add(Qt.Key.Key_M,      self._on_mute_toggle)
        # ── Edit transport ────────────────────────────────────────────────────
        self._add(Qt.Key.Key_Left,   self._on_prev_frame)
        self._add(Qt.Key.Key_Right,  self._on_next_frame)
        # ── Application ───────────────────────────────────────────────────────
        self._add(QKeySequence("."),       self._on_toggle_favorite)
        self._add_app(Qt.Key.Key_Escape,   self._on_exit_fullscreen)
        # fmt: on

    # ------------------------------------------------------------------
    # Context / routing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _focus_is_text_input() -> bool:
        """Return True when the currently focused widget accepts text input.

        Shortcuts that could conflict with typing (M, Space) should bail out
        early when this returns True.
        """
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QTextEdit, QAbstractSpinBox))

    def _is_video_visible(self) -> bool:
        return self._video_area is not None and self._video_area.has_video()

    def _edit_video_transport_active(self) -> bool:
        """Return True when the edit coordinator can handle transport shortcuts."""
        return self._edit is not None and self._edit.video_is_transport_active()

    # ------------------------------------------------------------------
    # Playback handlers
    # ------------------------------------------------------------------

    def _on_play_pause(self) -> None:
        if self._focus_is_text_input():
            return
        if self._video_area is None:
            return
        if self._edit_video_transport_active():
            # Edit view: use the trim-aware toggle
            self._edit.toggle_video_playback()  # type: ignore[union-attr]
            self._video_area.note_activity()
        elif self._router.is_detail_view_active() and self._is_video_visible():
            if self._video_area.is_playing():
                self._video_area.pause()
            else:
                self._video_area.play()
            self._video_area.note_activity()

    def _on_mute_toggle(self) -> None:
        if self._focus_is_text_input():
            return
        if self._video_area is None:
            return
        if not (self._router.is_detail_view_active() or self._router.is_edit_view_active()):
            return
        if not self._is_video_visible():
            return
        self._video_area.toggle_mute()
        self._video_area.note_activity()

    # ------------------------------------------------------------------
    # Edit transport handlers
    # ------------------------------------------------------------------

    def _on_prev_frame(self) -> None:
        if self._edit is not None:
            self._edit.step_video_frame(-1)

    def _on_next_frame(self) -> None:
        if self._edit is not None:
            self._edit.step_video_frame(1)

    # ------------------------------------------------------------------
    # Application handlers
    # ------------------------------------------------------------------

    def _on_toggle_favorite(self) -> None:
        self._toggle_favorite_cb()

    def _on_exit_fullscreen(self) -> None:
        self._exit_fullscreen_cb()
