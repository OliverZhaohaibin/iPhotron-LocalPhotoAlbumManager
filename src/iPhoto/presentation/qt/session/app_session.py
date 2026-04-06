"""GUI session state for the Qt presentation layer.

This module holds runtime session state that was previously scattered
inside ``AppContext``.  Separating it from the DI container allows each
concern to evolve independently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from ....config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE, WORK_DIR_NAME

if TYPE_CHECKING:  # pragma: no cover
    from ....gui.facade import AppFacade
    from ....library.manager import LibraryManager
    from ....settings.manager import SettingsManager

_logger = logging.getLogger(__name__)


@dataclass
class AppSession:
    """Holds per-launch GUI state: settings, library binding, facade, history."""

    settings: "SettingsManager"
    library: "LibraryManager"
    facade: "AppFacade"
    recent_albums: List[Path] = field(default_factory=list)
    theme: object = field(default=None)
    defer_startup_tasks: bool = False
    _pending_basic_library_path: Optional[Path] = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        from ....errors import LibraryError  # noqa: F401 – kept for callers that import via session

        basic_path = self.settings.get("basic_library_path")
        if isinstance(basic_path, str) and basic_path:
            self._pending_basic_library_path = Path(basic_path).expanduser()

        if not self.defer_startup_tasks:
            self.resume_startup_tasks()

        stored = self.settings.get("last_open_albums", []) or []
        resolved: list[Path] = []
        for entry in stored:
            try:
                resolved.append(Path(entry))
            except TypeError:
                continue
        if resolved:
            self.recent_albums = resolved[:10]

    def resume_startup_tasks(self) -> None:
        """Run deferred startup work such as binding the default library path."""

        from ....errors import LibraryError

        candidate = self._pending_basic_library_path
        self._pending_basic_library_path = None
        if candidate is None:
            _logger.info("AppSession.resume_startup_tasks: no pending library path")
            return
        _logger.info(
            "AppSession.resume_startup_tasks: attempting to bind saved library path %s", candidate
        )
        if candidate.exists():
            try:
                self.library.bind_path(candidate)
                _logger.info(
                    "AppSession.resume_startup_tasks: bind_path succeeded, root=%s",
                    self.library.root(),
                )
                self._start_initial_scan_if_needed(candidate)
            except LibraryError as exc:
                _logger.error("AppSession.resume_startup_tasks: bind_path failed: %s", exc)
                self.library.errorRaised.emit(str(exc))
        else:
            _logger.warning(
                "AppSession.resume_startup_tasks: saved path does not exist: %s", candidate
            )
            self.library.errorRaised.emit(
                f"Basic Library path is unavailable: {candidate}"
            )

    def _start_initial_scan_if_needed(self, library_root: Path) -> None:
        work_dir = library_root / WORK_DIR_NAME
        db_path = work_dir / "global_index.db"
        if work_dir.exists() and db_path.exists():
            return
        if self.library.is_scanning_path(library_root):
            return
        self.library.start_scanning(library_root, DEFAULT_INCLUDE, DEFAULT_EXCLUDE)

    def remember_album(self, root: Path) -> None:
        """Track *root* in the recent albums list, keeping the most recent first."""

        normalized = root.resolve()
        self.recent_albums = [entry for entry in self.recent_albums if entry != normalized]
        self.recent_albums.insert(0, normalized)
        del self.recent_albums[10:]
        self.settings.set(
            "last_open_albums",
            [str(path) for path in self.recent_albums],
        )
