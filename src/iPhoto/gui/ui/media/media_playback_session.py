"""Shared current-media session for detail playback and related UI."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtCore import QAbstractItemModel

from .playlist_controller import PlaylistController


class MediaPlaybackSession(QObject):
    """Own the current media selection for playback-adjacent UI."""

    currentChanged = Signal(int, object)
    restoreRequested = Signal(object, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._playlist = PlaylistController(self)
        self._playlist.currentChanged.connect(self._on_playlist_row_changed)
        self._playlist.sourceChanged.connect(self._on_playlist_source_changed)

    def bind_model(self, model: QAbstractItemModel) -> None:
        self._playlist.bind_model(model)

    def set_current_row(self, row: int) -> Optional[Path]:
        return self._playlist.set_current(row)

    def set_current_by_path(self, path: Path) -> bool:
        return self._playlist.set_current_by_path(path)

    def current_row(self) -> int:
        return self._playlist.current_row()

    def current_source(self) -> Optional[Path]:
        return self._playlist.current_source()

    def next_row(self) -> Optional[int]:
        return self._playlist.peek_next_row(1)

    def previous_row(self) -> Optional[int]:
        return self._playlist.peek_next_row(-1)

    def _on_playlist_row_changed(self, row: int) -> None:
        if row < 0:
            self.currentChanged.emit(-1, None)

    def _on_playlist_source_changed(self, path: Path) -> None:
        self.currentChanged.emit(self._playlist.current_row(), path)
