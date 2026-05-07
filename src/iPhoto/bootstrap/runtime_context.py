"""Formal runtime entry point for GUI startup and dependency wiring."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QTimer

from ..application.use_cases.scan_models import ScanMode
from ..events.bus import EventBus

if TYPE_CHECKING:  # pragma: no cover
    from ..di.container import DependencyContainer
    from ..gui.facade import AppFacade
    from ..gui.ui.theme_manager import ThemeManager
    from ..infrastructure.services.library_asset_runtime import LibraryAssetRuntime
    from ..library.runtime_controller import LibraryRuntimeController
    from ..settings.manager import SettingsManager
    from .library_session import LibrarySession

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PendingStartupScanResume:
    root: Path
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    mode: ScanMode
    allow_face_scan: bool | None = None


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
    facade: "AppFacade" = field(default_factory=_create_facade)
    event_bus: EventBus = field(default_factory=_create_event_bus)
    asset_runtime: "LibraryAssetRuntime" = field(default_factory=_create_asset_runtime)
    recent_albums: list[Path] = field(default_factory=list)
    defer_startup_tasks: bool = False
    theme: "ThemeManager" = field(init=False)
    library_session: "LibrarySession | None" = field(init=False, default=None)
    _container: "DependencyContainer | None" = field(
        init=False,
        default=None,
        repr=False,
    )
    _pending_basic_library_path: Path | None = field(init=False, default=None, repr=False)
    _pending_startup_scan_resume: _PendingStartupScanResume | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _startup_resume_candidate_timer: QTimer | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _startup_resume_idle_timer: QTimer | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _startup_resume_busy: bool = field(init=False, default=False, repr=False)
    _last_startup_resume_interaction_at: float = field(
        init=False,
        default_factory=time.monotonic,
        repr=False,
    )

    def __post_init__(self) -> None:
        self.theme = _create_theme_manager(self.settings)
        self.facade.bind_library(self.library)
        self._bind_startup_resume_signals()

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

    def _bind_startup_resume_signals(self) -> None:
        load_started = getattr(self.facade, "loadStarted", None)
        if load_started is not None:
            load_started.connect(lambda _root: self._set_startup_resume_busy(True))
        load_finished = getattr(self.facade, "loadFinished", None)
        if load_finished is not None:
            load_finished.connect(lambda _root, _success: self._set_startup_resume_busy(False))

    @classmethod
    def create(cls, *, defer_startup: bool = False) -> "RuntimeContext":
        """Create a runtime context for desktop startup."""

        return cls(defer_startup_tasks=defer_startup)

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
                scan_root = self.library.root() or candidate
                should_resume_scan = False
                if self.library_session is not None:
                    scan_service = getattr(self.library_session, "scans", None)
                    has_incomplete_scan = getattr(scan_service, "has_incomplete_scan", None)
                    if callable(has_incomplete_scan):
                        should_resume_scan = bool(has_incomplete_scan(scan_root))
                if (
                    (not had_existing_index or should_resume_scan)
                    and not self.library.is_scanning_path(scan_root)
                ):
                    self._schedule_startup_scan_resume(
                        scan_root,
                        include=tuple(DEFAULT_INCLUDE),
                        exclude=tuple(DEFAULT_EXCLUDE),
                        mode=ScanMode.INITIAL_SAFE,
                        # Let resume_scan() decide whether deferred pairing
                        # work should re-enable face indexing.
                        allow_face_scan=None,
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

    def note_user_interaction(self) -> None:
        """Record interactive activity that should defer startup resume work."""

        self._last_startup_resume_interaction_at = time.monotonic()
        idle_timer = getattr(self, "_startup_resume_idle_timer", None)
        if (
            self._pending_startup_scan_resume is not None
            and idle_timer is not None
            and idle_timer.isActive()
        ):
            idle_timer.start(self._startup_idle_delay_ms())

    def resume_pending_startup_scan_now(self) -> bool:
        """Start the deferred startup scan immediately when one is queued."""

        pending = self._pending_startup_scan_resume
        if pending is None:
            return False
        if self.library.is_scanning_path(pending.root):
            self._pending_startup_scan_resume = None
            return False
        self._stop_startup_resume_timers()
        try:
            self.facade.scan_root_async(
                pending.root,
                include=pending.include,
                exclude=pending.exclude,
                mode=pending.mode,
                allow_face_scan=pending.allow_face_scan,
            )
        except TypeError:
            self.facade.scan_root_async(
                pending.root,
                include=pending.include,
                exclude=pending.exclude,
                mode=pending.mode,
            )
        self._pending_startup_scan_resume = None
        return True

    def _schedule_startup_scan_resume(
        self,
        root: Path,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...],
        mode: ScanMode,
        allow_face_scan: bool | None,
    ) -> None:
        self._pending_startup_scan_resume = _PendingStartupScanResume(
            root=Path(root),
            include=tuple(include),
            exclude=tuple(exclude),
            mode=mode,
            allow_face_scan=allow_face_scan,
        )
        self._last_startup_resume_interaction_at = time.monotonic()
        startup_notice = getattr(self.facade, "startupResumePending", None)
        if startup_notice is not None:
            startup_notice.emit(
                "发现未完成扫描，界面空闲后会继续。需要立刻继续时可点击 Rescan。"
            )
        if QCoreApplication.instance() is None:
            return
        self._ensure_startup_resume_timers()
        if self._startup_resume_candidate_timer is not None:
            self._startup_resume_candidate_timer.start(self._startup_candidate_delay_ms())

    def _ensure_startup_resume_timers(self) -> None:
        if self._startup_resume_candidate_timer is None:
            self._startup_resume_candidate_timer = QTimer()
            self._startup_resume_candidate_timer.setSingleShot(True)
            self._startup_resume_candidate_timer.timeout.connect(
                self._on_startup_resume_candidate_timeout
            )
        if self._startup_resume_idle_timer is None:
            self._startup_resume_idle_timer = QTimer()
            self._startup_resume_idle_timer.setSingleShot(True)
            self._startup_resume_idle_timer.timeout.connect(
                self._on_startup_resume_idle_timeout
            )

    def _set_startup_resume_busy(self, busy: bool) -> None:
        self._startup_resume_busy = bool(busy)
        if not busy and self._pending_startup_scan_resume is not None:
            candidate_timer = getattr(self, "_startup_resume_candidate_timer", None)
            if candidate_timer is not None and not candidate_timer.isActive():
                candidate_timer.start(self._startup_candidate_retry_ms())

    def _on_startup_resume_candidate_timeout(self) -> None:
        if self._pending_startup_scan_resume is None:
            return
        if self._startup_resume_busy or self._startup_resume_user_active():
            if self._startup_resume_candidate_timer is not None:
                self._startup_resume_candidate_timer.start(self._startup_candidate_retry_ms())
            return
        if self._startup_resume_idle_timer is not None:
            self._startup_resume_idle_timer.start(self._startup_idle_delay_ms())

    def _on_startup_resume_idle_timeout(self) -> None:
        if self._pending_startup_scan_resume is None:
            return
        if self._startup_resume_busy or self._startup_resume_user_active():
            if self._startup_resume_idle_timer is not None:
                self._startup_resume_idle_timer.start(self._startup_idle_delay_ms())
            return
        self.resume_pending_startup_scan_now()

    def _startup_resume_user_active(self) -> bool:
        idle_seconds = self._startup_idle_delay_ms() / 1000.0
        return time.monotonic() - self._last_startup_resume_interaction_at < idle_seconds

    def _stop_startup_resume_timers(self) -> None:
        candidate_timer = getattr(self, "_startup_resume_candidate_timer", None)
        if candidate_timer is not None:
            candidate_timer.stop()
        idle_timer = getattr(self, "_startup_resume_idle_timer", None)
        if idle_timer is not None:
            idle_timer.stop()

    @staticmethod
    def _startup_candidate_delay_ms() -> int:
        return 1500

    @staticmethod
    def _startup_candidate_retry_ms() -> int:
        return 500

    @staticmethod
    def _startup_idle_delay_ms() -> int:
        return 2000

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
            if callable(bind_path_from_session):
                bind_path_from_session(normalized)
            else:
                self.library.bind_path(normalized)
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

        self._pending_startup_scan_resume = None
        self._stop_startup_resume_timers()

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
