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
    from .library_session import LibrarySession

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
    library_session: "LibrarySession | None" = field(init=False, default=None)
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
                existing_work_dir = resolve_work_dir(candidate)
                had_existing_index = (
                    existing_work_dir is not None
                    and (existing_work_dir / "global_index.db").exists()
                )
                self.open_library(candidate)
                _logger.info(
                    "resume_startup_tasks: bind_path succeeded, root=%s",
                    self.library.root(),
                )
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

    def open_library(self, root: Path) -> "LibrarySession":
        """Bind *root* as the active library and rebuild library-scoped adapters."""

        from .library_session import LibrarySession
        from ..errors import LibraryUnavailableError

        normalized = Path(root).expanduser().resolve()
        self.close_library()

        if not normalized.exists() or not normalized.is_dir():
            raise LibraryUnavailableError(f"Library path does not exist: {root}")

        self.library_session = LibrarySession(
            normalized,
            asset_runtime=self.asset_runtime,
            bind_asset_runtime=False,
        )
        bind_asset_query_service = getattr(
            self.library,
            "bind_asset_query_service",
            None,
        )
        if callable(bind_asset_query_service):
            bind_asset_query_service(self.library_session.asset_queries)
        bind_state_repository = getattr(self.library, "bind_state_repository", None)
        if callable(bind_state_repository):
            bind_state_repository(self.library_session.state)
        bind_album_metadata_service = getattr(
            self.library,
            "bind_album_metadata_service",
            None,
        )
        if callable(bind_album_metadata_service):
            bind_album_metadata_service(self.library_session.album_metadata)

        try:
            self.library.bind_path(normalized)
        except Exception:
            self.close_library()
            raise

        self.asset_runtime.bind_library_root(normalized)

        bind_scan_service = getattr(self.library, "bind_scan_service", None)
        if callable(bind_scan_service):
            bind_scan_service(self.library_session.scans)
        bind_asset_lifecycle_service = getattr(
            self.library,
            "bind_asset_lifecycle_service",
            None,
        )
        if callable(bind_asset_lifecycle_service):
            bind_asset_lifecycle_service(self.library_session.asset_lifecycle)
        bind_asset_operation_service = getattr(
            self.library,
            "bind_asset_operation_service",
            None,
        )
        if callable(bind_asset_operation_service):
            bind_asset_operation_service(self.library_session.asset_operations)
        bind_people_service = getattr(self.library, "bind_people_service", None)
        if callable(bind_people_service):
            bind_people_service(self.library_session.people)
        return self.library_session

    def close_library(self) -> None:
        """Close the active library-scoped session if one exists."""

        bind_asset_lifecycle_service = getattr(
            self.library,
            "bind_asset_lifecycle_service",
            None,
        )
        if callable(bind_asset_lifecycle_service):
            bind_asset_lifecycle_service(None)

        bind_asset_operation_service = getattr(
            self.library,
            "bind_asset_operation_service",
            None,
        )
        if callable(bind_asset_operation_service):
            bind_asset_operation_service(None)

        bind_people_service = getattr(self.library, "bind_people_service", None)
        if callable(bind_people_service):
            bind_people_service(None)

        bind_state_repository = getattr(self.library, "bind_state_repository", None)
        if callable(bind_state_repository):
            bind_state_repository(None)

        bind_album_metadata_service = getattr(
            self.library,
            "bind_album_metadata_service",
            None,
        )
        if callable(bind_album_metadata_service):
            bind_album_metadata_service(None)

        bind_asset_query_service = getattr(
            self.library,
            "bind_asset_query_service",
            None,
        )
        if callable(bind_asset_query_service):
            bind_asset_query_service(None)

        bind_scan_service = getattr(self.library, "bind_scan_service", None)
        if callable(bind_scan_service):
            bind_scan_service(None)

        session = getattr(self, "library_session", None)
        if session is None:
            return
        session.shutdown()
        self.library_session = None

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
