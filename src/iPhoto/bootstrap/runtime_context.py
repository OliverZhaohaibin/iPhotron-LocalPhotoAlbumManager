"""Formal runtime entry point for GUI startup and dependency wiring."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..events.bus import EventBus
from .startup_profile import mark

if TYPE_CHECKING:  # pragma: no cover
    from ..di.container import DependencyContainer
    from ..gui.facade import AppFacade
    from ..gui.i18n import TranslationManager
    from ..gui.ui.theme_manager import ThemeManager
    from ..infrastructure.services.library_asset_runtime import LibraryAssetRuntime
    from ..library.runtime_controller import LibraryRuntimeController
    from ..settings.manager import SettingsManager
    from .library_session import LibrarySession

_logger = logging.getLogger(__name__)


def _create_settings_manager() -> "SettingsManager":
    from ..settings.manager import SettingsManager

    manager = SettingsManager()
    manager.load()
    return manager


def _create_library_manager() -> "LibraryRuntimeController":
    from ..library.runtime_controller import LibraryRuntimeController

    return LibraryRuntimeController()


def _create_facade() -> "AppFacade":
    from ..gui.facade import AppFacade

    return AppFacade()


def _create_theme_manager(settings: "SettingsManager") -> "ThemeManager":
    from ..gui.ui.theme_manager import ThemeManager

    theme = ThemeManager(settings)
    theme.apply_theme()
    return theme


def _create_translation_manager(settings: "SettingsManager") -> "TranslationManager":
    from ..gui.i18n import TranslationManager

    translation = TranslationManager(settings)
    translation.apply_language()
    return translation


def _create_asset_runtime() -> "LibraryAssetRuntime":
    from ..infrastructure.services.library_asset_runtime import LibraryAssetRuntime

    return LibraryAssetRuntime()


def _create_event_bus() -> EventBus:
    return EventBus(logging.getLogger("EventBus"))


@dataclass
class RuntimeContext:
    """Authoritative runtime dependency bundle for GUI startup."""

    settings: "SettingsManager" = field(default_factory=_create_settings_manager)
    library: "LibraryRuntimeController" = field(default_factory=_create_library_manager)
    event_bus: EventBus = field(default_factory=_create_event_bus)
    recent_albums: list[Path] = field(default_factory=list)
    defer_startup_tasks: bool = False
    translation: "TranslationManager" = field(init=False)
    theme: "ThemeManager" = field(init=False)
    library_session: "LibrarySession | None" = field(init=False, default=None)
    _facade: "AppFacade | None" = field(init=False, default=None, repr=False)
    _asset_runtime: "LibraryAssetRuntime | None" = field(init=False, default=None, repr=False)
    _container: "DependencyContainer | None" = field(
        init=False,
        default=None,
        repr=False,
    )
    _pending_basic_library_path: Path | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self.translation = _create_translation_manager(self.settings)
        self.theme = _create_theme_manager(self.settings)

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
    def create(
        cls,
        *,
        defer_startup: bool = False,
        settings: "SettingsManager | None" = None,
    ) -> "RuntimeContext":
        """Create a runtime context for desktop startup."""

        if settings is None:
            return cls(defer_startup_tasks=defer_startup)
        return cls(settings=settings, defer_startup_tasks=defer_startup)

    @property
    def facade(self) -> "AppFacade":
        """Create the service-heavy GUI facade on first actual use."""

        if self._facade is None:
            self._facade = _create_facade()
            self._facade.bind_library(self.library)
        return self._facade

    @facade.setter
    def facade(self, value: "AppFacade") -> None:
        # Setter preserves compatibility with tests and lightweight embedders
        # that assemble RuntimeContext through ``__new__``.
        self._facade = value

    @property
    def asset_runtime(self) -> "LibraryAssetRuntime":
        """Create repositories and thumbnail services on first actual use."""

        if self._asset_runtime is None:
            self._asset_runtime = _create_asset_runtime()
        return self._asset_runtime

    @asset_runtime.setter
    def asset_runtime(self, value: "LibraryAssetRuntime") -> None:
        self._asset_runtime = value

    @property
    def container(self) -> "DependencyContainer":
        """Return an empty DI container for callers that still inspect it."""

        if self._container is None:
            from ..di.container import DependencyContainer

            self._container = DependencyContainer()
        return self._container

    def resume_startup_tasks(self) -> None:
        """Run deferred startup work such as binding the default library path."""

        from ..config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
        from ..errors import LibraryError

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
                self.open_library(candidate)
                _logger.info(
                    "resume_startup_tasks: bind_path succeeded, root=%s",
                    self.library.root(),
                )
                scan_service = getattr(self.library, "scan_service", None)
                is_scan_scope_complete = getattr(
                    scan_service,
                    "is_scan_scope_complete",
                    None,
                )
                scan_complete = False
                if callable(is_scan_scope_complete):
                    scan_complete = bool(is_scan_scope_complete(candidate))
                if not scan_complete and not self.library.is_scanning_path(candidate):
                    self.facade.scan_root_async(
                        candidate,
                        include=DEFAULT_INCLUDE,
                        exclude=DEFAULT_EXCLUDE,
                    )
            except LibraryError as exc:
                _logger.error("resume_startup_tasks: bind_path failed: %s", exc)
                self.library.errorRaised.emit(str(exc))
        else:
            _logger.warning(
                "resume_startup_tasks: saved path does not exist: %s",
                candidate,
            )
            self.library.errorRaised.emit(f"Basic Library path is unavailable: {candidate}")

    def open_library(self, root: Path) -> "LibrarySession":
        """Bind *root* as the active library and rebuild library-scoped adapters."""

        from .library_session import LibrarySession
        from ..errors import LibraryUnavailableError

        normalized = Path(root).expanduser().resolve()
        self.close_library()

        if not normalized.exists() or not normalized.is_dir():
            raise LibraryUnavailableError(f"Library path does not exist: {root}")

        mark("library_session.before_create", root=str(normalized))
        self.library_session = LibrarySession(
            normalized,
            asset_runtime=self.asset_runtime,
            bind_asset_runtime=False,
        )
        mark("library_session.created", root=str(normalized))
        bind_library_session = getattr(self.library, "bind_library_session", None)
        used_session_binding = callable(bind_library_session)
        if used_session_binding:
            bind_library_session(self.library_session)
        else:
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
            bind_asset_state_service = getattr(
                self.library,
                "bind_asset_state_service",
                None,
            )
            if callable(bind_asset_state_service):
                bind_asset_state_service(self.library_session.asset_state)
            bind_album_metadata_service = getattr(
                self.library,
                "bind_album_metadata_service",
                None,
            )
            if callable(bind_album_metadata_service):
                bind_album_metadata_service(self.library_session.album_metadata)
            bind_location_service = getattr(self.library, "bind_location_service", None)
            if callable(bind_location_service):
                bind_location_service(self.library_session.locations)
            bind_edit_service = getattr(self.library, "bind_edit_service", None)
            if callable(bind_edit_service):
                bind_edit_service(self.library_session.edit)

        try:
            bind_path_from_session = getattr(self.library, "bind_path_from_session", None)
            mark("library.bind_path.before", root=str(normalized))
            if callable(bind_path_from_session):
                bind_path_from_session(normalized)
            else:
                self.library.bind_path(normalized)
            mark("library.bind_path.after", root=str(normalized))
        except Exception:
            self.close_library()
            raise

        self.asset_runtime.bind_library_root(normalized)
        if not used_session_binding:
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
            bind_map_runtime = getattr(self.library, "bind_map_runtime", None)
            if callable(bind_map_runtime):
                bind_map_runtime(self.library_session.maps)
            bind_map_interaction_service = getattr(
                self.library,
                "bind_map_interaction_service",
                None,
            )
            if callable(bind_map_interaction_service):
                bind_map_interaction_service(self.library_session.map_interactions)
        return self.library_session

    def close_library(self) -> None:
        """Close the active library-scoped session if one exists."""

        bind_library_session = getattr(self.library, "bind_library_session", None)
        if callable(bind_library_session):
            bind_library_session(None)
        else:
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
            bind_map_runtime = getattr(self.library, "bind_map_runtime", None)
            if callable(bind_map_runtime):
                bind_map_runtime(None)
            bind_map_interaction_service = getattr(
                self.library,
                "bind_map_interaction_service",
                None,
            )
            if callable(bind_map_interaction_service):
                bind_map_interaction_service(None)

            bind_location_service = getattr(self.library, "bind_location_service", None)
            if callable(bind_location_service):
                bind_location_service(None)

            bind_state_repository = getattr(self.library, "bind_state_repository", None)
            if callable(bind_state_repository):
                bind_state_repository(None)
            bind_asset_state_service = getattr(
                self.library,
                "bind_asset_state_service",
                None,
            )
            if callable(bind_asset_state_service):
                bind_asset_state_service(None)

            bind_album_metadata_service = getattr(
                self.library,
                "bind_album_metadata_service",
                None,
            )
            if callable(bind_album_metadata_service):
                bind_album_metadata_service(None)
            bind_edit_service = getattr(self.library, "bind_edit_service", None)
            if callable(bind_edit_service):
                bind_edit_service(None)

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
        self.recent_albums = [entry for entry in self.recent_albums if entry != normalized]
        self.recent_albums.insert(0, normalized)
        del self.recent_albums[10:]
        self.settings.set(
            "last_open_albums",
            [str(path) for path in self.recent_albums],
        )


__all__ = ["RuntimeContext"]
