# watch_filesystem.py
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .base import UseCase, UseCaseRequest, UseCaseResponse
from iPhoto.events.bus import EventBus


@dataclass(frozen=True)
class WatchFilesystemRequest(UseCaseRequest):
    action: str = "start"  # "start", "stop", "pause", "resume"
    watch_paths: list[str] = field(default_factory=list)
    album_id: str = ""


@dataclass(frozen=True)
class WatchFilesystemResponse(UseCaseResponse):
    watched_paths: list[str] = field(default_factory=list)
    is_watching: bool = False


class WatchFilesystemUseCase(UseCase):
    """Manages filesystem watching state for album directories.

    .. note::

       This is a **state-management** use case that tracks which paths
       are watched and whether watching is paused.  Actual OS-level
       filesystem monitoring (e.g. via ``QFileSystemWatcher``) is handled
       by :class:`~iPhoto.library.filesystem_watcher.FileSystemWatcherMixin`
       in the library layer.  The ``on_change_callback`` is invoked by the
       library-layer watcher when a directory change is detected.
    """

    def __init__(
        self,
        event_bus: EventBus,
        on_change_callback: Optional[Callable[[str], None]] = None,
    ):
        self._event_bus = event_bus
        self._on_change = on_change_callback
        self._watched: set[str] = set()
        self._paused = False
        self._logger = logging.getLogger(__name__)

    def execute(self, request: WatchFilesystemRequest) -> WatchFilesystemResponse:
        if request.action == "start":
            return self._start_watching(request)
        elif request.action == "stop":
            return self._stop_watching(request)
        elif request.action == "pause":
            self._paused = True
            return WatchFilesystemResponse(
                watched_paths=sorted(self._watched),
                is_watching=not self._paused,
            )
        elif request.action == "resume":
            self._paused = False
            return WatchFilesystemResponse(
                watched_paths=sorted(self._watched),
                is_watching=not self._paused,
            )
        return WatchFilesystemResponse(success=False, error=f"Unknown action: {request.action}")

    def _start_watching(self, request: WatchFilesystemRequest) -> WatchFilesystemResponse:
        for p in request.watch_paths:
            path = Path(p)
            if path.is_dir():
                self._watched.add(str(path))
        self._paused = False
        return WatchFilesystemResponse(
            watched_paths=sorted(self._watched),
            is_watching=True,
        )

    def _stop_watching(self, request: WatchFilesystemRequest) -> WatchFilesystemResponse:
        paths_to_remove = set(request.watch_paths) if request.watch_paths else set(self._watched)
        self._watched -= paths_to_remove
        return WatchFilesystemResponse(
            watched_paths=sorted(self._watched),
            is_watching=bool(self._watched) and not self._paused,
        )
