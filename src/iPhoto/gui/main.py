"""GUI entry point for the iPhoto desktop application."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from iPhoto.bootstrap.startup_profile import configure as configure_startup_profile
from iPhoto.bootstrap.startup_profile import mark

mark("module.before_qt_imports")
from PySide6.QtCore import QEvent, QObject, QTimer, Qt, Signal  # noqa: E402, I001
from PySide6.QtGui import QColor, QPalette, QSurfaceFormat  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from iPhoto.bootstrap.qt_shader_cache import configure_shader_cache_environment  # noqa: E402
from iPhoto.gui.render_backend import should_configure_global_desktop_opengl  # noqa: E402

mark("module.imported")

_logger = logging.getLogger(__name__)
_QUEUED_CONNECTION = Qt.ConnectionType.QueuedConnection
_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_MACOS_EXTERNAL_TOOL_PATHS = (
    Path("/opt/homebrew/bin"),
    Path("/opt/homebrew/sbin"),
    Path("/usr/local/bin"),
    Path("/usr/local/sbin"),
    Path("/opt/local/bin"),
    Path("/opt/local/sbin"),
)
_STARTUP_GALLERY_WARMUP_FALLBACK_MS = 3000
_STARTUP_HANG_DIAG_ENV = "IPHOTO_STARTUP_HANG_DIAG"
_STARTUP_HANG_DIAG_TIMEOUT_SECONDS = 15
_STARTUP_INPUT_EVENT_TYPES = frozenset(
    event_type
    for name in (
        "MouseButtonPress",
        "MouseButtonRelease",
        "MouseButtonDblClick",
        "MouseMove",
        "Wheel",
        "KeyPress",
        "KeyRelease",
        "Shortcut",
        "ShortcutOverride",
        "ContextMenu",
        "TabletPress",
        "TabletMove",
        "TabletRelease",
        "TouchBegin",
        "TouchUpdate",
        "TouchEnd",
        "TouchCancel",
        "NativeGesture",
    )
    if (event_type := getattr(QEvent.Type, name, None)) is not None
)


class _StartupTimingPlan(NamedTuple):
    first_post_paint_delay_ms: int
    feature_interval_ms: int
    coordinator_ready_delay_ms: int


class _StartupImportRegistry:
    """Publish background import results without leaking across retries."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[int, object] = {}
        self._errors: dict[int, Exception] = {}

    def publish(self, generation: int, value: object) -> None:
        with self._lock:
            self._values[generation] = value

    def fail(self, generation: int, error: Exception) -> None:
        with self._lock:
            self._errors[generation] = error

    def ready(self, generation: int) -> bool:
        with self._lock:
            return generation in self._values or generation in self._errors

    def resolve(self, generation: int) -> object:
        with self._lock:
            error = self._errors.get(generation)
            if error is not None:
                raise error
            return self._values[generation]

    def discard(self, generation: int) -> None:
        with self._lock:
            self._values.pop(generation, None)
            self._errors.pop(generation, None)


class _StartupModulePreloader(QObject):
    """Run startup imports off the GUI thread without blocking shutdown."""

    settled = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._registry = _StartupImportRegistry()
        self._lock = threading.Lock()
        self._threads: dict[int, threading.Thread] = {}
        self._cancelled: set[int] = set()
        self._closed = False

    def start(
        self,
        generation: int,
        loader: Callable[[], object],
        *,
        asynchronous: bool = True,
    ) -> bool:
        generation = int(generation)
        with self._lock:
            if self._closed or generation in self._cancelled:
                return False
            existing = self._threads.get(generation)
            if existing is not None and existing.is_alive():
                return False

        def _load() -> None:
            accepted = False
            try:
                value = loader()
            except Exception as exc:  # noqa: BLE001 - import isolation boundary
                with self._lock:
                    accepted = not self._closed and generation not in self._cancelled
                    if accepted:
                        self._registry.fail(generation, exc)
            else:
                with self._lock:
                    accepted = not self._closed and generation not in self._cancelled
                    if accepted:
                        self._registry.publish(generation, value)
            finally:
                with self._lock:
                    self._threads.pop(generation, None)
                if accepted:
                    self.settled.emit(generation)

        if not asynchronous:
            _load()
            return True
        thread = threading.Thread(
            target=_load,
            name=f"StartupModulePreloader-{generation}",
            daemon=True,
        )
        with self._lock:
            self._threads[generation] = thread
        thread.start()
        return True

    def ready(self, generation: int) -> bool:
        return self._registry.ready(generation)

    def resolve(self, generation: int) -> object:
        return self._registry.resolve(generation)

    def cancel_generation(self, generation: int) -> None:
        generation = int(generation)
        with self._lock:
            self._cancelled.add(generation)
        self._registry.discard(generation)

    def close(self, *, timeout_ms: int = 1500) -> tuple[str, ...]:
        """Cancel publication and wait for workers only up to ``timeout_ms``."""

        with self._lock:
            self._closed = True
            self._cancelled.update(self._threads)
            threads = tuple(self._threads.values())
        deadline = time.monotonic() + max(0, int(timeout_ms)) / 1000.0
        for thread in threads:
            if thread is threading.current_thread():
                continue
            thread.join(max(0.0, deadline - time.monotonic()))
        lingering = tuple(thread.name for thread in threads if thread.is_alive())
        if lingering:
            _logger.warning(
                "Startup import workers exceeded the shutdown deadline: %s",
                ", ".join(lingering),
            )
        return lingering


class _StartupInputGuard(QObject):
    """Temporarily discard early input while Linux finishes GUI startup."""

    def __init__(self, window: QObject, app: QApplication) -> None:
        try:
            super().__init__(window)
        except TypeError:
            # Unit tests use light fake windows; production always passes a QObject.
            super().__init__()
        self._window = window
        self._app = app
        self._active = False
        self._installed = False

    def install(self) -> None:
        """Install this event filter if the application object supports it."""

        if self._installed:
            self._active = True
            return
        install_filter = getattr(self._app, "installEventFilter", None)
        if callable(install_filter):
            install_filter(self)
            self._installed = True
        self._active = True

    def release(self) -> None:
        """Stop filtering startup input and detach from the application."""

        self._active = False
        if not self._installed:
            return
        remove_filter = getattr(self._app, "removeEventFilter", None)
        if callable(remove_filter):
            try:
                remove_filter(self)
            except RuntimeError:
                pass
        self._installed = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not self._active:
            return False
        if event.type() not in _STARTUP_INPUT_EVENT_TYPES:
            return False
        return self._belongs_to_window(watched)

    def _belongs_to_window(self, watched: QObject) -> bool:
        if watched is self._window:
            return True

        is_ancestor = getattr(self._window, "isAncestorOf", None)
        if callable(is_ancestor):
            try:
                if is_ancestor(watched):
                    return True
            except (RuntimeError, TypeError):
                pass

        current = watched
        while current is not None:
            if current is self._window:
                return True
            parent = getattr(current, "parent", None)
            if not callable(parent):
                return False
            try:
                current = parent()
            except RuntimeError:
                return False
        return False


def _bootstrap_macos_external_tool_path() -> None:
    """Expose common Homebrew/MacPorts tool paths to GUI-launched app bundles."""

    if sys.platform != "darwin":
        return

    # Use the target platform's PATH separator rather than the host process
    # separator so darwin-specific normalization also behaves correctly in
    # cross-platform tests that monkeypatch ``sys.platform``.
    path_separator = ":"

    existing_tool_paths: list[str] = []
    for candidate in _MACOS_EXTERNAL_TOOL_PATHS:
        try:
            if candidate.is_dir():
                existing_tool_paths.append(candidate.as_posix())
        except OSError:
            continue

    current_paths = [
        entry
        for entry in os.environ.get("PATH", "").split(path_separator)
        if entry
    ]
    merged_paths: list[str] = []
    seen: set[str] = set()
    for entry in [*existing_tool_paths, *current_paths]:
        if entry in seen:
            continue
        seen.add(entry)
        merged_paths.append(entry)
    if merged_paths:
        os.environ["PATH"] = path_separator.join(merged_paths)


def _configure_qt_shader_disk_cache(library_root: Path | None = None) -> None:
    """Route shader/program caches into a managed ``.iPhoto`` work directory."""
    if library_root is None:
        try:
            configure_shader_cache_environment(use_saved_library=False)
        except TypeError:
            # Compatibility for embedders/tests that replace the helper with a
            # historical no-argument callable.
            configure_shader_cache_environment()
    else:
        configure_shader_cache_environment(library_root=library_root)


def _opengl_explicitly_disabled() -> bool:
    """Return whether all OpenGL-backed UI surfaces should be disabled."""

    return os.environ.get("IPHOTO_DISABLE_OPENGL", "").strip().lower() in _TRUE_ENV_VALUES


def _startup_hang_diagnostics_enabled() -> bool:
    """Return whether verbose startup hang diagnostics are enabled."""

    return os.environ.get(_STARTUP_HANG_DIAG_ENV, "").strip().lower() in _TRUE_ENV_VALUES


def _enable_startup_hang_diagnostics() -> None:
    """Enable opt-in traceback dumps for startup freezes."""

    if not _startup_hang_diagnostics_enabled():
        return
    try:
        import faulthandler

        faulthandler.dump_traceback_later(
            _STARTUP_HANG_DIAG_TIMEOUT_SECONDS,
            repeat=True,
        )
        _logger.info(
            "Startup hang diagnostics enabled; dumping thread stacks every %ss",
            _STARTUP_HANG_DIAG_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001
        _logger.warning("Failed to enable startup hang diagnostics", exc_info=True)


def _map_gl_surface_format(platform: str | None = None) -> QSurfaceFormat:
    """Return the conservative OpenGL surface format used by map widgets."""

    platform = sys.platform if platform is None else platform
    surface_format = QSurfaceFormat()
    surface_format.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    surface_format.setDepthBufferSize(24)
    surface_format.setStencilBufferSize(8)
    surface_format.setAlphaBufferSize(8 if platform == "darwin" else 0)
    surface_format.setSamples(0)
    return surface_format


def _is_packaged_runtime() -> bool:
    """Return ``True`` when the app is running from a compiled/frozen bundle."""

    return "__compiled__" in globals() or getattr(sys, "frozen", False)


def _benchmark_auto_exit_delay_ms() -> int | None:
    """Return the opt-in benchmark shutdown delay, or ``None`` in normal runs."""

    value = os.environ.get("IPHOTO_STARTUP_BENCHMARK_AUTO_EXIT_MS", "").strip()
    if not value:
        return None
    try:
        return max(0, min(10_000, int(value)))
    except ValueError:
        return None


def _allow_packaged_linux_wayland() -> bool:
    """Return whether packaged Linux builds may keep Qt's default platform selection."""

    raw_value = os.environ.get("IPHOTO_ALLOW_PACKAGED_LINUX_WAYLAND", "").strip().lower()
    return raw_value in _TRUE_ENV_VALUES


def _prefer_local_source_tree() -> None:
    """Ensure direct script runs import the workspace package first.

    When ``main.py`` is launched directly from an IDE, Python may resolve the
    editable ``iPhoto`` install from another checkout before this repo's
    ``src`` tree. Prepending the local ``src`` path keeps the GUI aligned with
    the code being edited.
    """

    src_root = Path(__file__).resolve().parents[2]
    src_root_str = str(src_root)
    if sys.path and sys.path[0] == src_root_str:
        return
    try:
        sys.path.remove(src_root_str)
    except ValueError:
        pass
    sys.path.insert(0, src_root_str)


def _prepare_qt_runtime_for_maps() -> None:
    """Respect the desktop platform; optional Maps must not decide app startup."""

    if sys.platform != "linux":
        return

    if _opengl_explicitly_disabled():
        return

    # A user/launcher may explicitly choose XCB.  Configure its GL integration
    # in that case, but never force a Wayland session onto XCB merely because a
    # native map extension happens to be packaged.
    if os.environ.get("QT_QPA_PLATFORM") == "xcb":
        os.environ.setdefault("QT_OPENGL", "desktop")
        os.environ.setdefault("QT_XCB_GL_INTEGRATION", "xcb_glx")


def _configure_qt_opengl_defaults(library_root: Path | None = None) -> None:
    """Apply OpenGL context defaults required by the map widgets."""

    _configure_qt_shader_disk_cache(library_root)

    if _opengl_explicitly_disabled():
        return

    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    except Exception:  # noqa: BLE001, S110
        pass

    if should_configure_global_desktop_opengl():
        try:
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL, True)
        except Exception:  # noqa: BLE001, S110
            pass

    try:
        QSurfaceFormat.setDefaultFormat(_map_gl_surface_format())
    except Exception:  # noqa: BLE001
        return


def _startup_feature_plan(
    platform: str | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return features created before and after the main window is shown.

    On OpenGL-backed desktop platforms, inserting the detail page's
    ``QRhiWidget`` children into an already visible top-level widget can make
    Qt recreate the native window. That appears as a short-lived first window
    followed by the real one. Keep the GPU-backed detail page in the pre-show
    phase there, while retaining the faster first-frame path on macOS.
    """

    target_platform = sys.platform if platform is None else platform
    deferred = ("detail",)
    if target_platform in {"win32", "linux"}:
        return (("detail",), ())
    return ((), deferred)


def _startup_timing_plan(platform: str | None = None) -> _StartupTimingPlan:
    """Return post-paint startup delays for the target platform."""

    del platform
    return _StartupTimingPlan(0, 0, 0)


def main(argv: list[str] | None = None) -> int:
    """Launch the Qt application and return the exit code."""

    _prefer_local_source_tree()
    _bootstrap_macos_external_tool_path()
    mark("main.entered")

    # Ensure the ``iPhoto`` root logger is configured before any component
    # creates a child logger.  ``get_logger()`` lazily attaches a StreamHandler
    # to the ``iPhoto`` logger so all ``iPhoto.*`` loggers propagate output to
    # stderr at INFO level by default.
    from iPhoto.utils.logging import get_logger as _init_logging
    _init_logging()
    _enable_startup_hang_diagnostics()

    arguments = list(sys.argv if argv is None else argv)
    if len(arguments) > 2 and arguments[1] == "--startup-library-probe":
        from iPhoto.bootstrap.library_probe import _main as _run_library_probe

        return _run_library_probe(arguments[2:])
    from iPhoto.bootstrap.bootstrap_settings import load_bootstrap_settings

    bootstrap_settings = load_bootstrap_settings()
    mark("bootstrap_settings.loaded", error=bootstrap_settings.load_error)
    _prepare_qt_runtime_for_maps()
    _configure_qt_opengl_defaults()
    mark("qapplication.before_create")
    app = QApplication(arguments)
    platform_name = getattr(app, "platformName", None)
    qt_backend = (
        platform_name()
        if callable(platform_name)
        else os.environ.get("QT_QPA_PLATFORM", "unknown")
    )
    configure_startup_profile(
        qt_backend=qt_backend,
        graphics_backend=(
            os.environ.get("IPHOTO_STARTUP_GRAPHICS_BACKEND")
            or os.environ.get("IPHOTO_RHI_BACKEND")
            or os.environ.get("QT_OPENGL")
            or "default"
        ),
        runtime="packaged" if _is_packaged_runtime() else "source",
    )
    mark("qapplication.created")

    from iPhoto.bootstrap.gui_startup_job_queue import GuiStartupJobQueue
    from iPhoto.bootstrap.startup_orchestrator import (
        StartupFailure,
        StartupOrchestrator,
        StartupPhase,
    )

    startup = StartupOrchestrator(app if isinstance(app, QObject) else None)
    startup.begin()
    startup.transition(StartupPhase.APP_CREATED)
    startup_jobs = GuiStartupJobQueue(
        app if isinstance(app, QObject) else None,
        is_generation_current=startup.is_current,
    )
    startup_imports = _StartupModulePreloader(
        app if isinstance(app, QObject) else None,
    )
    startup_imports.settled.connect(
        startup_jobs.wake_for_generation,
        _QUEUED_CONNECTION,
    )

    def _handle_startup_job_failure(failure) -> None:
        if not startup.is_current(failure.generation):
            return
        phase = startup.phase
        if phase in {
            StartupPhase.DEGRADED,
            StartupPhase.FAILED,
            StartupPhase.CANCELLED,
        }:
            return
        startup.fail(
            StartupFailure(
                phase=phase,
                message=str(failure.exception) or type(failure.exception).__name__,
                exception_type=type(failure.exception).__name__,
            )
        )

    startup_jobs.jobFailed.connect(_handle_startup_job_failure)

    from iPhoto.settings.manager import SettingsManager

    try:
        startup_settings = SettingsManager(path=bootstrap_settings.path)
    except TypeError:  # lightweight embedders and tests may expose a no-arg factory
        startup_settings = SettingsManager()
    recovery_loader = getattr(startup_settings, "load_with_recovery", None)
    if callable(recovery_loader):
        settings_recovery_warning = recovery_loader()
    else:
        startup_settings.load()
        settings_recovery_warning = None
    mark("settings.loaded", recovered=bool(settings_recovery_warning))

    # ``QToolTip`` instances inherit ``WA_TranslucentBackground`` from the frameless
    # main window, which means they expect the application to provide an opaque fill
    # colour.  Some Qt styles ignore stylesheet rules for tooltips, so we proactively
    # update the palette that drives those popups to guarantee readable text.
    tooltip_palette = QPalette(app.palette())

    def _resolved_colour(source: QColor, fallback: QColor) -> QColor:
        """Return a copy of *source* with a fully opaque alpha channel.

        Qt reports transparent colours for certain palette roles when
        ``WA_TranslucentBackground`` is active.  Failing to normalise the alpha value
        causes the compositor to blend the tooltip against the desktop wallpaper,
        producing the solid black rectangle described in the regression report.
        Falling back to a well-tested default keeps the tooltip legible even on
        themes that omit one of the roles we query.
        """

        if not source.isValid():
            return QColor(fallback)

        resolved = QColor(source)
        resolved.setAlpha(255)
        return resolved

    base_colour = _resolved_colour(
        tooltip_palette.color(QPalette.ColorRole.Window), QColor("#eef3f6")
    )
    text_colour = _resolved_colour(
        tooltip_palette.color(QPalette.ColorRole.WindowText), QColor(Qt.GlobalColor.black)
    )

    # Ensure the text remains readable by checking the lightness contrast.  When the
    # palette provides nearly identical shades we fall back to a simple dark-on-light
    # scheme that mirrors Qt's built-in defaults.
    if abs(base_colour.lightness() - text_colour.lightness()) < 40:
        base_colour = QColor("#eef3f6")
        text_colour = QColor(Qt.GlobalColor.black)

    tooltip_palette.setColor(QPalette.ColorRole.ToolTipBase, base_colour)
    tooltip_palette.setColor(QPalette.ColorRole.ToolTipText, text_colour)
    app.setPalette(tooltip_palette, "QToolTip")

    from iPhoto.bootstrap.runtime_context import RuntimeContext

    mark("runtime_context.imported")
    from iPhoto.gui.ui.main_window import MainWindow

    mark("main_window.imported")

    # Defer heavy library binding + initial scan until the event loop is running.
    context = RuntimeContext.create(defer_startup=True, settings=startup_settings)
    mark("runtime_context.created")
    # --- Phase 4: Coordinator Wiring ---
    window = MainWindow(context)
    mark("main_window.created")
    set_startup_orchestrator = getattr(window, "set_startup_orchestrator", None)
    if callable(set_startup_orchestrator):
        set_startup_orchestrator(startup)
    if settings_recovery_warning:
        QTimer.singleShot(
            0,
            lambda: window.show_startup_recovery(
                settings_recovery_warning,
                details=settings_recovery_warning,
            ),
        )
    startup_input_guard = _StartupInputGuard(window, app)
    startup_input_guard.install()

    from iPhoto.bootstrap.library_probe import LibraryProbeController

    probe_controller = LibraryProbeController(window if isinstance(window, QObject) else None)

    def _handle_startup_phase_changed(snapshot) -> None:
        if snapshot.phase in {
            StartupPhase.DEGRADED,
            StartupPhase.FAILED,
            StartupPhase.CANCELLED,
        }:
            startup_jobs.cancel_generation(snapshot.generation)
            startup_imports.cancel_generation(snapshot.generation)
        if snapshot.phase in {
            StartupPhase.DEGRADED,
            StartupPhase.FAILED,
            StartupPhase.CANCELLED,
        }:
            probe_controller.cancel()

    startup.phaseChanged.connect(_handle_startup_phase_changed)

    pre_show_features, post_show_features = _startup_feature_plan()
    startup_timing = _startup_timing_plan()

    def _preload_startup_modules() -> object:
        if "detail" in post_show_features:
            import importlib

            importlib.import_module("iPhoto.gui.ui.widgets.detail_page")
        from iPhoto.gui.coordinators.main_coordinator import MainCoordinator

        return MainCoordinator

    def _start_startup_imports(generation: int) -> None:
        startup_imports.start(
            generation,
            _preload_startup_modules,
            asynchronous=isinstance(app, QObject),
        )

    _start_startup_imports(startup.generation)
    for feature in pre_show_features:
        job_name = f"feature.{feature}.pre_show"
        thread_name = threading.current_thread().name
        mark(
            "startup.gui_job.started",
            job=job_name,
            generation=startup.generation,
            duration_ms=0.0,
            budget_ms=100.0,
            over_budget=False,
            thread=thread_name,
            result="running",
        )
        started_ns = time.perf_counter_ns()
        feature_error = False
        try:
            if feature == "detail":
                mark("rhi_detail.before_create")
            window.ui.ensure_feature(feature)
            if feature == "detail":
                mark("rhi_detail.created")
        except Exception:
            feature_error = True
            raise
        finally:
            duration_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0
            details = {
                "job": job_name,
                "generation": startup.generation,
                "duration_ms": round(duration_ms, 3),
                "budget_ms": 100.0,
                "over_budget": duration_ms > 100.0,
                "thread": thread_name,
                "result": "error" if feature_error else "success",
            }
            mark("startup.gui_job.finished", **details)
            if duration_ms > 100.0:
                mark("startup.gui_stall", **details)
                _logger.warning(
                    "GUI startup job %s exceeded 100.0ms budget (%.1fms)",
                    job_name,
                    duration_ms,
                )

    # Coordinator needs Window, Context, and Container
    coordinator = None
    coordinator_started = False

    def _enqueue_startup_job(
        name: str,
        generation: int,
        callback,
        *,
        prerequisite=None,
    ) -> bool:
        return startup_jobs.enqueue(
            name,
            generation,
            callback,
            prerequisite=prerequisite,
        )

    def _continue_after_library_ready(generation: int) -> None:
        if not startup.is_current(generation):
            return
        startup.transition(StartupPhase.LIBRARY_READY)
        startup_scan_enqueued = False

        def _run_idle_startup_jobs() -> None:
            if not startup.is_current(generation):
                return
            startup.transition(StartupPhase.GALLERY_READY)
            starter = getattr(context, "schedule_idle_startup_jobs", None)
            if not callable(starter):
                starter = getattr(context, "start_deferred_startup_scan", None)
            if callable(starter):
                _logger.info("Starting deferred startup scan")
                starter()
            startup.complete()

        def _enqueue_idle_startup_jobs() -> None:
            nonlocal startup_scan_enqueued
            if startup_scan_enqueued or not startup.is_current(generation):
                return
            startup_scan_enqueued = _enqueue_startup_job(
                "startup.idle_jobs",
                generation,
                _run_idle_startup_jobs,
            )

        def _arm_startup_gallery_warmup() -> bool:
            model_getter = getattr(coordinator, "gallery_startup_model", None)
            model = model_getter() if callable(model_getter) else None
            begin_warmup = getattr(model, "begin_startup_gallery_warmup", None)
            if not callable(begin_warmup):
                begin_warmup = getattr(model, "begin_startup_first_frame_gate", None)
            ready_signal = getattr(model, "startupGalleryReady", None)
            if ready_signal is None:
                ready_signal = getattr(model, "startupFirstFrameReady", None)
            connect = getattr(ready_signal, "connect", None)
            if not callable(begin_warmup) or not callable(connect):
                return False
            connect(_enqueue_idle_startup_jobs)
            begin_warmup()
            return True

        startup_input_guard.release()
        warmup_armed = _arm_startup_gallery_warmup()
        if warmup_armed:
            QTimer.singleShot(
                _STARTUP_GALLERY_WARMUP_FALLBACK_MS,
                _enqueue_idle_startup_jobs,
            )

        def _select_initial_collection() -> None:
            mark("startup_gallery.selection_requested")
            if len(arguments) > 1:
                coordinator.open_album_from_path(Path(arguments[1]))
            else:
                window.ui.sidebar.select_all_photos(emit_signal=True)
            if not warmup_armed:
                _enqueue_idle_startup_jobs()

        _enqueue_startup_job(
            "gallery.select_initial",
            generation,
            _select_initial_collection,
        )

    def _start_library_probe(generation: int) -> None:
        if not startup.is_current(generation):
            return
        _logger.info("Coordinator ready; resuming startup tasks")
        startup_input_guard.release()
        request_getter = getattr(context, "request_startup_library_probe", None)
        committer = getattr(context, "commit_prepared_library", None)
        request = request_getter() if callable(request_getter) else None
        startup.transition(StartupPhase.LIBRARY_PROBING)
        mark("startup.probe.started", generation=generation)
        if request is None or not callable(committer):
            context.resume_startup_tasks(defer_scan=True)
            mark(
                "startup.probe.finished",
                generation=generation,
                result="unbound",
                storage_kind="unbound",
            )
            _continue_after_library_ready(generation)
            return

        expected_request_id = request.request_id

        def _probe_ready(prepared) -> None:
            if (
                not startup.is_current(generation)
                or prepared.request_id != expected_request_id
            ):
                return
            mark(
                "startup.probe.finished",
                generation=generation,
                result="success",
                storage_kind=getattr(prepared, "storage_kind", "unknown"),
                warnings=prepared.warnings,
            )

            def _commit_prepared_library() -> None:
                committer(prepared, defer_scan=True)
                if "migration_restored" in prepared.warnings:
                    window.show_startup_warning(
                        "The photo library index was restored after an interrupted update.",
                        details="code=migration_restored",
                    )
                _continue_after_library_ready(generation)

            _enqueue_startup_job(
                "library.commit",
                generation,
                _commit_prepared_library,
            )

        def _probe_failed(failure) -> None:
            if (
                not startup.is_current(generation)
                or failure.request_id != expected_request_id
            ):
                return
            mark(
                "startup.probe.finished",
                generation=generation,
                result="failure",
                code=failure.code,
            )
            startup.fail(
                StartupFailure(
                    phase=StartupPhase.LIBRARY_PROBING,
                    message=failure.message,
                    exception_type=failure.exception_type,
                    recoverable=failure.recoverable,
                    code=failure.code,
                    suggested_action=failure.suggested_action,
                )
            )

        probe_controller.ready.connect(_probe_ready)
        probe_controller.failed.connect(_probe_failed)
        probe_controller.start(request)

    def _construct_coordinator(generation: int) -> None:
        nonlocal coordinator
        mark("post_paint.begin")
        mark("main_coordinator.imported")
        if coordinator is None:
            _logger.info("Creating MainCoordinator")
            coordinator_factory = startup_imports.resolve(generation)
            if not callable(coordinator_factory):
                raise RuntimeError("startup coordinator import returned no factory")
            coordinator = coordinator_factory(window, context)
            window.set_coordinator(coordinator)

    def _start_coordinator() -> None:
        nonlocal coordinator_started
        if coordinator is None or coordinator_started:
            return
        coordinator.start()
        coordinator_started = True
        mark("main_coordinator.started")

    def _initialize_features_after_show(generation: int) -> None:
        # QWidget construction stays on the GUI thread, one named job per turn.
        for feature in post_show_features:
            def _create_feature(feature_name=feature) -> None:
                mark("feature.before_create", feature=feature_name)
                window.ui.ensure_feature(feature_name)
                mark("feature.created", feature=feature_name)

            _enqueue_startup_job(
                f"feature.{feature}.post_show",
                generation,
                _create_feature,
            )
        _enqueue_startup_job(
            "coordinator.construct",
            generation,
            lambda: _construct_coordinator(generation),
            prerequisite=lambda: startup_imports.ready(generation),
        )
        _enqueue_startup_job(
            "coordinator.start",
            generation,
            _start_coordinator,
            prerequisite=lambda: coordinator is not None,
        )
        _enqueue_startup_job(
            "library.probe.start",
            generation,
            lambda: _start_library_probe(generation),
            prerequisite=lambda: coordinator_started,
        )

    def _continue_after_shell() -> None:
        startup_input_guard.release()
        generation = startup.generation
        if startup_timing.first_post_paint_delay_ms > 0:
            QTimer.singleShot(
                startup_timing.first_post_paint_delay_ms,
                lambda: _initialize_features_after_show(generation),
            )
            return
        _initialize_features_after_show(generation)

    def _retry_startup() -> None:
        if startup.phase is StartupPhase.CANCELLED:
            return
        generation = startup.begin()
        startup.transition(StartupPhase.INTERACTIVE, reason="retry")
        _start_startup_imports(generation)
        _initialize_features_after_show(generation)

    startup.startupDegraded.connect(
        lambda failure: window.show_startup_recovery(
            failure.message,
            details=(
                f"phase={failure.phase.value}; "
                f"code={failure.code}; "
                f"exception={failure.exception_type or 'unknown'}"
            ),
            retry_callback=_retry_startup,
            suggested_action=failure.suggested_action,
        )
    )
    benchmark_exit_delay_ms = _benchmark_auto_exit_delay_ms()
    benchmark_exit_scheduled = False

    def _schedule_benchmark_exit(_payload=None) -> None:
        nonlocal benchmark_exit_scheduled
        if benchmark_exit_delay_ms is None or benchmark_exit_scheduled:
            return
        benchmark_exit_scheduled = True
        QTimer.singleShot(benchmark_exit_delay_ms, window.close)

    startup.startupCompleted.connect(_schedule_benchmark_exit)
    startup.startupDegraded.connect(_schedule_benchmark_exit)
    window.firstPainted.connect(startup.first_painted)
    # Arm before show(): test doubles and a few embedded Qt hosts can paint
    # synchronously from show(), and that event must not be lost.
    startup.shell_shown(_continue_after_shell)
    mark("startup.show", generation=startup.generation)
    window.show()
    mark("main_window.show_called")

    try:
        return app.exec()
    finally:
        startup.cancel()
        startup_jobs.close()
        startup_imports.close()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
