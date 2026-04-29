"""Formal runtime entry point for GUI startup and dependency wiring."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .container import build_container

if TYPE_CHECKING:  # pragma: no cover
    from ..di.container import DependencyContainer
    from ..gui.facade import AppFacade
    from ..gui.ui.theme_manager import ThemeManager
    from ..infrastructure.services.library_asset_runtime import LibraryAssetRuntime
    from ..library.manager import LibraryManager
    from ..settings.manager import SettingsManager

_logger = logging.getLogger(__name__)


def _create_settings_manager() -> "SettingsManager":
    from ..settings.manager import SettingsManager

    manager = SettingsManager()
    manager.load()
    return manager


def _create_library_manager() -> "LibraryManager":
    from ..library.manager import LibraryManager

    return LibraryManager()


def _create_facade() -> "AppFacade":
    from ..gui.facade import AppFacade

    return AppFacade()


def _create_theme_manager(settings: "SettingsManager") -> "ThemeManager":
    from ..gui.ui.theme_manager import ThemeManager

    theme = ThemeManager(settings)
    theme.apply_theme()
    return theme


def _create_asset_runtime() -> "LibraryAssetRuntime":
    from ..infrastructure.services.library_asset_runtime import LibraryAssetRuntime

    return LibraryAssetRuntime()


@dataclass
class RuntimeContext:
    """Authoritative runtime dependency bundle for GUI startup."""

    settings: "SettingsManager" = field(default_factory=_create_settings_manager)
    library: "LibraryManager" = field(default_factory=_create_library_manager)
    facade: "AppFacade" = field(default_factory=_create_facade)
    container: "DependencyContainer" = field(default_factory=build_container)
    asset_runtime: "LibraryAssetRuntime" = field(default_factory=_create_asset_runtime)
    recent_albums: list[Path] = field(default_factory=list)
    defer_startup_tasks: bool = False
    theme: "ThemeManager" = field(init=False)
    _pending_basic_library_path: Path | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self.theme = _create_theme_manager(self.settings)
        self.facade.bind_library(self.library)

        basic_path = self.settings.get("basic_library_path")
        if isinstance(basic_path, str) and basic_path:
            self._pending_basic_library_path = Path(basic_path).expanduser()

        stored = self.settings.get("last_open_albums", []) or []
        resolved: list[Path] = []
        for entry in stored:
            try:
                resolved.append(Path(entry))
            except TypeError:
                continue
        if resolved:
            self.recent_albums = resolved[:10]

        if not self.defer_startup_tasks:
            self.resume_startup_tasks()

    @classmethod
    def create(cls, *, defer_startup: bool = False) -> "RuntimeContext":
        """Create a runtime context for desktop startup."""

        return cls(defer_startup_tasks=defer_startup)

    def resume_startup_tasks(self) -> None:
        """Run deferred startup work such as binding the default library path."""

        from ..config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
        from ..errors import LibraryError
        from ..utils.pathutils import resolve_work_dir

        candidate = self._pending_basic_library_path
        self._pending_basic_library_path = None
        if candidate is None:
            _logger.info("resume_startup_tasks: no pending library path")
            return
        _logger.info(
            "resume_startup_tasks: attempting to bind saved library path %s",
            candidate,
        )
        if candidate.exists():
            try:
                self.library.bind_path(candidate)
                _logger.info(
                    "resume_startup_tasks: bind_path succeeded, root=%s",
                    self.library.root(),
                )
                existing_work_dir = resolve_work_dir(candidate)
                had_existing_index = (
                    existing_work_dir is not None
                    and (existing_work_dir / "global_index.db").exists()
                )
                self.asset_runtime.bind_library_root(candidate)
                if (
                    not had_existing_index
                    and not self.library.is_scanning_path(candidate)
                ):
                    self.library.start_scanning(
                        candidate,
                        DEFAULT_INCLUDE,
                        DEFAULT_EXCLUDE,
                    )
            except LibraryError as exc:
                _logger.error("resume_startup_tasks: bind_path failed: %s", exc)
                self.library.errorRaised.emit(str(exc))
        else:
            _logger.warning(
                "resume_startup_tasks: saved path does not exist: %s",
                candidate,
            )
            self.library.errorRaised.emit(
                f"Basic Library path is unavailable: {candidate}"
            )

    def remember_album(self, root: Path) -> None:
        """Track *root* in the recent albums list, keeping the most recent first."""

        normalized = root.resolve()
        self.recent_albums = [
            entry for entry in self.recent_albums if entry != normalized
        ]
        self.recent_albums.insert(0, normalized)
        del self.recent_albums[10:]
        self.settings.set(
            "last_open_albums",
            [str(path) for path in self.recent_albums],
        )


__all__ = ["RuntimeContext"]
