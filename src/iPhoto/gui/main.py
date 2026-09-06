"""GUI entry point for the iPhoto desktop application."""

from __future__ import annotations

import importlib
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
from PySide6.QtGui import QColor, QPalette, QSurface, QSurfaceFormat  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from iPhoto.bootstrap.qt_shader_cache import configure_shader_cache_environment  # noqa: E402
from iPhoto.gui.render_backend import (  # noqa: E402
    selected_rhi_backend_name,
    should_configure_global_desktop_opengl,
)

mark("module.imported")

_logger = logging.getLogger(__name__)
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
_QUEUED_CONNECTION = Qt.ConnectionType.QueuedConnection
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
    """Own background imports and reject results from cancelled generations."""

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

    from iPhoto.runtime_diagnostics import enable_runtime_diagnostics

    if enable_runtime_diagnostics():
        return
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
    # The frameless top-level uses WA_TranslucentBackground on macOS and
    # Windows. OpenGL composition must not proactively request a zero-bit alpha
    # surface on either platform.
    surface_format.setAlphaBufferSize(8 if platform in {"darwin", "win32"} else 0)
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
    """Return feature completion work scheduled after the first paint.

    Detail's final QRhi hierarchy is prepared separately before ``show()`` on
    every desktop platform.  Only its non-native feature completion remains in
    this plan; platform-specific exceptions are intentionally forbidden.
    """

    del platform
    return ((), ("detail",))


def _prepare_top_level_rhi_surface(window: object, backend_name: str) -> str:
    """Create the native top-level with the surface type required by QRhi.

    Ui_MainWindow attaches the QRhi hierarchy before QMenuBar causes native
    QWindow creation.  This function is a read-only contract check: changing,
    destroying, or recreating QWidget's internal QWindow here would detach its
    backing store and leave QRhiWidget without the window-associated QRhi.
    """

    targets_by_backend = {
        "metal": (QSurface.SurfaceType.MetalSurface,),
        "direct3d11": (QSurface.SurfaceType.Direct3DSurface,),
        "d3d11": (QSurface.SurfaceType.Direct3DSurface,),
        "opengl": (
            QSurface.SurfaceType.OpenGLSurface,
            QSurface.SurfaceType.RasterGLSurface,
        ),
    }
    targets = targets_by_backend.get(str(backend_name).strip().lower())
    if targets is None:
        raise RuntimeError(f"Unsupported startup QRhi backend: {backend_name}")
    preferred_target = targets[0]

    window_handle_accessor = getattr(window, "windowHandle", None)
    if not callable(window_handle_accessor):
        return preferred_target.name
    handle = window_handle_accessor()
    if handle is None:
        # QMenuBar creates a native handle during setup on macOS, while other
        # platforms may correctly defer it until show().  The attached QRhi
        # hierarchy will select the surface type when Qt creates that handle.
        return f"deferred:{preferred_target.name}"

    is_visible = getattr(window, "isVisible", None)
    if callable(is_visible) and is_visible():
        raise RuntimeError("Top-level QRhi surface must be configured before show()")
    actual_surface_type = handle.surfaceType()
    if actual_surface_type not in targets:
        raise RuntimeError(
            "Top-level surface was created with "
            f"{actual_surface_type.name}; expected one of "
            f"{', '.join(target.name for target in targets)}"
        )
    return actual_surface_type.name


def _top_level_graphics_contract(
    window: object,
    backend_name: str,
    *,
    platform: str | None = None,
) -> dict[str, object]:
    """Inspect the created top-level graphics contract after its first paint."""

    platform = sys.platform if platform is None else platform
    handle_accessor = getattr(window, "windowHandle", None)
    handle = handle_accessor() if callable(handle_accessor) else None
    surface_type = "missing"
    actual_alpha_bits = -1
    if handle is not None:
        surface_type_getter = getattr(handle, "surfaceType", None)
        if callable(surface_type_getter):
            surface_type_value = surface_type_getter()
            surface_type = getattr(surface_type_value, "name", str(surface_type_value))
        format_getter = getattr(handle, "format", None)
        if callable(format_getter):
            actual_format = format_getter()
            alpha_getter = getattr(actual_format, "alphaBufferSize", None)
            if callable(alpha_getter):
                actual_alpha_bits = int(alpha_getter())

    test_attribute = getattr(window, "testAttribute", None)
    translucent = bool(
        callable(test_attribute)
        and test_attribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    )
    flags_getter = getattr(window, "windowFlags", None)
    flags = flags_getter() if callable(flags_getter) else Qt.WindowType.Widget
    frameless = bool(flags & Qt.WindowType.FramelessWindowHint)
    payload = {
        "backend": str(backend_name),
        "surface_type": surface_type,
        "actual_alpha_bits": actual_alpha_bits,
        "translucent": translucent,
        "frameless": frameless,
    }
    mark("main_window.graphics_contract", **payload)
    _logger.info("Main-window graphics contract: %s", payload)
    if (
        platform == "win32"
        and str(backend_name).strip().lower() == "opengl"
        and translucent
        and actual_alpha_bits <= 0
    ):
        _logger.warning(
            "Windows translucent OpenGL top-level has no confirmed alpha buffer: %s. "
            "Re-run with QT_LOGGING_RULES=qt.qpa.gl=true;qt.rhi.*=true",
            payload,
        )
    return payload


def _startup_timing_plan(platform: str | None = None) -> _StartupTimingPlan:
    """Return post-paint startup delays for the target platform."""

    del platform
    return _StartupTimingPlan(0, 0, 0)


def _configure_tooltip_palette(app: QApplication) -> None:
    """Install an opaque, readable tooltip palette for the frameless shell."""

    tooltip_palette = QPalette(app.palette())

    def _resolved_colour(source: QColor, fallback: QColor) -> QColor:
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
    if abs(base_colour.lightness() - text_colour.lightness()) < 40:
        base_colour = QColor("#eef3f6")
        text_colour = QColor(Qt.GlobalColor.black)
    tooltip_palette.setColor(QPalette.ColorRole.ToolTipBase, base_colour)
    tooltip_palette.setColor(QPalette.ColorRole.ToolTipText, text_colour)
    app.setPalette(tooltip_palette, "QToolTip")


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
    startup.begin()
    startup.transition(StartupPhase.APP_CREATED)

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

    try:
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
    except Exception as exc:  # Startup terminal boundary.
        _logger.exception("Unable to load application settings")
        startup.fail(
            StartupFailure(
                phase=StartupPhase.APP_CREATED,
                message=str(exc) or type(exc).__name__,
                exception_type=type(exc).__name__,
                recoverable=False,
                code="settings_initialization_failed",
                suggested_action="continue_without_library",
            )
        )
        startup_jobs.close()
        startup_imports.close()
        return 1
    mark("settings.loaded", recovered=bool(settings_recovery_warning))

    try:
        _configure_tooltip_palette(app)
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
        startup_input_guard = _StartupInputGuard(window, app)
        startup_input_guard.install()

        from iPhoto.bootstrap.library_probe import LibraryProbeController

        probe_controller = LibraryProbeController(
            window if isinstance(window, QObject) else None
        )
    except Exception as exc:  # Startup terminal boundary.
        _logger.exception("Unable to construct the application shell")
        startup.fail(
            StartupFailure(
                phase=StartupPhase.APP_CREATED,
                message=str(exc) or type(exc).__name__,
                exception_type=type(exc).__name__,
                recoverable=False,
                code="shell_initialization_failed",
                suggested_action="continue_without_library",
            )
        )
        startup_jobs.close()
        startup_imports.close()
        return 1
    if settings_recovery_warning:
        QTimer.singleShot(
            0,
            lambda: window.show_startup_recovery(
                settings_recovery_warning,
                details=settings_recovery_warning,
            ),
        )
    active_probe: dict[str, object] = {}

    def _clear_active_probe(generation: int) -> None:
        if int(active_probe.get("generation", -1)) == generation:
            active_probe.clear()

    def _register_attempt_resources(generation: int) -> None:
        startup.register_cleanup(
            lambda generation=generation: startup_jobs.cancel_generation(generation)
        )
        startup.register_cleanup(
            lambda generation=generation: startup_imports.cancel_generation(generation)
        )
        startup.register_cleanup(probe_controller.cancel)
        startup.register_cleanup(startup_input_guard.release)
        startup.register_cleanup(
            lambda generation=generation: _clear_active_probe(generation)
        )

    _register_attempt_resources(startup.generation)

    def _handle_startup_phase_changed(snapshot) -> None:
        if snapshot.phase in {
            StartupPhase.DEGRADED,
            StartupPhase.FAILED,
            StartupPhase.CANCELLED,
        }:
            startup_jobs.cancel_generation(snapshot.generation)
        if snapshot.phase in {
            StartupPhase.DEGRADED,
            StartupPhase.FAILED,
            StartupPhase.CANCELLED,
        }:
            probe_controller.cancel()

    startup.phaseChanged.connect(_handle_startup_phase_changed)

    _pre_show_features, post_show_features = _startup_feature_plan()
    startup_timing = _startup_timing_plan()

    def _preload_startup_modules() -> object:
        if "detail" in post_show_features:
            importlib.import_module("iPhoto.gui.ui.widgets.detail_page")
            importlib.import_module("iPhoto.gui.ui.widgets.gl_image_viewer")
        module = importlib.import_module(
            "iPhoto.gui.coordinators.desktop_coordinator_runtime"
        )
        return module.DesktopCoordinatorRuntime

    def _start_startup_imports(generation: int) -> None:
        if coordinator_runtime is not None:
            return
        startup_imports.start(
            generation,
            _preload_startup_modules,
            asynchronous=isinstance(app, QObject),
        )

    def _startup_imports_ready(generation: int) -> bool:
        return coordinator_runtime is not None or startup_imports.ready(generation)

    def _verify_prepared_native_hierarchy(
        generation: int,
    ) -> StartupFailure | None:
        job_name = "feature.detail.native_hierarchy.pre_show"
        thread_name = threading.current_thread().name
        mark(
            "startup.gui_job.started",
            job=job_name,
            generation=generation,
            duration_ms=0.0,
            budget_ms=100.0,
            over_budget=False,
            thread=thread_name,
            result="running",
        )
        mark("detail.native_hierarchy.before_verify", generation=generation)
        started_ns = time.perf_counter_ns()
        prepare_exception: Exception | None = None
        surface_count = 0
        graphics_backends: tuple[str, ...] = ()
        top_level_surface_type = "unknown"
        try:
            selected_backend = selected_rhi_backend_name()
            top_level_surface_type = _prepare_top_level_rhi_surface(
                window,
                selected_backend,
            )
            detail_page = getattr(window.ui, "_prepared_detail_page", None)
            if detail_page is None:
                raise RuntimeError(
                    "main window UI did not prepare the Detail native hierarchy"
                )
            surfaces = tuple(detail_page.native_surfaces())
            surface_count = len(surfaces)
            graphics_backends = tuple(
                sorted(
                    {
                        str(backend_name())
                        for surface in surfaces
                        if callable(
                            backend_name := getattr(
                                surface,
                                "render_backend_name",
                                None,
                            )
                        )
                    }
                )
            )
            mark(
                "detail.native_hierarchy.prepared",
                generation=generation,
                surface_count=surface_count,
                graphics_backends=graphics_backends,
                top_level_surface_type=top_level_surface_type,
            )
        except Exception as exc:  # Non-recoverable shell verification boundary.
            prepare_exception = exc
            _logger.exception("Pre-show Detail native hierarchy verification failed")
        finally:
            measured_duration_ms = (
                time.perf_counter_ns() - started_ns
            ) / 1_000_000.0
            prepared_duration_ms = float(
                getattr(window.ui, "_detail_native_prepare_duration_ms", 0.0)
            )
            duration_ms = max(measured_duration_ms, prepared_duration_ms)
            details = {
                "job": job_name,
                "generation": generation,
                "duration_ms": round(duration_ms, 3),
                "budget_ms": 100.0,
                "over_budget": duration_ms > 100.0,
                "thread": thread_name,
                "result": "error" if prepare_exception is not None else "success",
                "surface_count": surface_count,
                "graphics_backends": graphics_backends,
                "top_level_surface_type": top_level_surface_type,
            }
            mark("startup.gui_job.finished", **details)
            if duration_ms > 100.0:
                mark("startup.gui_stall", **details)
                _logger.warning(
                    "GUI startup job %s exceeded 100.0ms budget (%.1fms)",
                    job_name,
                    duration_ms,
                )
        if prepare_exception is not None:
            return StartupFailure(
                phase=StartupPhase.APP_CREATED,
                message=str(prepare_exception) or type(prepare_exception).__name__,
                exception_type=type(prepare_exception).__name__,
                recoverable=False,
                code="shell_initialization_failed",
                suggested_action="continue_without_library",
                operation=job_name,
            )
        return None

    shell_verification_failure = _verify_prepared_native_hierarchy(
        startup.generation
    )
    if shell_verification_failure is not None:
        startup.fail(shell_verification_failure)
        startup_jobs.close()
        startup_imports.close()
        return 1

    coordinator_runtime = None
    coordinator_started = False
    detail_benchmark_harness = None

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
            enable_detail_warmup = getattr(
                coordinator_runtime,
                "enable_detail_interaction_warmup",
                None,
            )
            if callable(enable_detail_warmup):
                enable_detail_warmup()

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
            gallery = getattr(coordinator_runtime, "gallery", None)
            model_getter = getattr(gallery, "startup_model", None)
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
            warmup_fallback_state = {"active": True}

            def _run_warmup_fallback() -> None:
                if warmup_fallback_state["active"]:
                    _enqueue_idle_startup_jobs()

            startup.register_cleanup(
                lambda: warmup_fallback_state.update(active=False)
            )
            QTimer.singleShot(
                _STARTUP_GALLERY_WARMUP_FALLBACK_MS,
                _run_warmup_fallback,
            )

        def _select_initial_collection() -> None:
            mark("startup_gallery.selection_requested")
            if len(arguments) > 1:
                coordinator_runtime.gallery.open_album_from_path(Path(arguments[1]))
            else:
                window.ui.sidebar.select_all_photos(emit_signal=True)
            if not warmup_armed:
                _enqueue_idle_startup_jobs()

        _enqueue_startup_job(
            "gallery.select_initial",
            generation,
            _select_initial_collection,
        )

    def _probe_ready(validated) -> None:
        generation = int(active_probe.get("generation", -1))
        expected_request_id = active_probe.get("request_id")
        committer = active_probe.get("committer")
        if (
            not startup.is_current(generation)
            or validated.request_id != expected_request_id
            or not callable(committer)
        ):
            return
        active_probe.clear()
        mark(
            "startup.probe.finished",
            generation=generation,
            result="success",
            storage_kind=getattr(validated, "storage_kind", "unknown"),
            warnings=validated.warnings,
        )

        def _commit_validated_library() -> None:
            committer(validated, defer_scan=True)
            restored = "migration_restored" in validated.warnings
            cleanup_pending = "migration_cleanup_pending" in validated.warnings
            if restored and cleanup_pending:
                window.show_startup_warning(
                    "The photo library was recovered and opened, but temporary "
                    "migration files could not be removed. They will be cleaned up later.",
                    details="code=migration_restored,migration_cleanup_pending",
                )
            elif restored:
                window.show_startup_warning(
                    "The photo library index was restored after an interrupted update.",
                    details="code=migration_restored",
                )
            elif cleanup_pending:
                window.show_startup_warning(
                    "The photo library opened, but temporary migration files could not "
                    "be removed. They will be cleaned up on a later start.",
                    details="code=migration_cleanup_pending",
                )
            _continue_after_library_ready(generation)

        _enqueue_startup_job(
            "library.commit",
            generation,
            _commit_validated_library,
        )

    def _probe_failed(failure) -> None:
        generation = int(active_probe.get("generation", -1))
        expected_request_id = active_probe.get("request_id")
        if (
            not startup.is_current(generation)
            or failure.request_id != expected_request_id
        ):
            return
        active_probe.clear()
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
                operation=failure.operation,
                native_code=failure.native_code,
            )
        )

    probe_controller.ready.connect(_probe_ready)
    probe_controller.failed.connect(_probe_failed)

    def _start_library_probe(generation: int) -> None:
        if not startup.is_current(generation):
            return
        _logger.info("Coordinator ready; resuming startup tasks")
        startup_input_guard.release()
        request_getter = getattr(context, "request_startup_library_probe", None)
        committer = getattr(context, "commit_validated_library", None)
        if not callable(committer):
            committer = getattr(context, "commit_prepared_library", None)
        request = request_getter() if callable(request_getter) else None
        startup.transition(StartupPhase.LIBRARY_PROBING)
        mark("startup.probe.started", generation=generation)
        if request is None or not callable(committer):
            active_probe.clear()
            context.resume_startup_tasks(defer_scan=True)
            mark(
                "startup.probe.finished",
                generation=generation,
                result="unbound",
                storage_kind="unbound",
            )
            _continue_after_library_ready(generation)
            return
        active_probe.clear()
        active_probe.update(
            generation=generation,
            request_id=request.request_id,
            committer=committer,
        )
        probe_controller.start(request)

    def _construct_coordinator(generation: int) -> None:
        nonlocal coordinator_runtime
        mark("post_paint.begin")
        coordinator_factory = None
        if coordinator_runtime is None:
            coordinator_factory = startup_imports.resolve(generation)
        mark("desktop_coordinator_runtime.imported")
        if coordinator_runtime is None:
            _logger.info("Creating DesktopCoordinatorRuntime")
            if not callable(coordinator_factory):
                raise RuntimeError("desktop coordinator import returned no factory")
            coordinator_runtime = coordinator_factory(window, context)
            window.bind_coordinators(
                coordinator_runtime,
                coordinator_runtime.gallery,
                coordinator_runtime.detail,
            )

    def _start_coordinator() -> None:
        nonlocal coordinator_started, detail_benchmark_harness
        if coordinator_runtime is None or coordinator_started:
            return
        coordinator_runtime.start()
        coordinator_started = True
        if os.environ.get("IPHOTO_DETAIL_BENCHMARK_PLAN", "").strip():
            from iPhoto.gui.detail_benchmark_harness import maybe_start_detail_benchmark

            detail_benchmark_harness = maybe_start_detail_benchmark(
                app,
                coordinator_runtime,
            )
        mark("desktop_coordinator_runtime.started")

    def _initialize_features_after_show(generation: int) -> None:
        # QWidget construction stays on the GUI thread, one named job per turn.
        _enqueue_startup_job(
            "startup.imports.start",
            generation,
            lambda: _start_startup_imports(generation),
        )
        for feature in post_show_features:
            def _create_feature(feature_name=feature) -> None:
                mark("feature.before_create", feature=feature_name)
                created = window.ui.ensure_feature(feature_name)
                mark("feature.created", feature=feature_name)
                if feature_name == "detail":
                    native_surfaces = getattr(created, "native_surfaces", None)
                    surfaces = tuple(native_surfaces()) if callable(native_surfaces) else ()
                    mark(
                        "detail.feature.completed",
                        generation=generation,
                        surface_count=len(surfaces),
                    )

            _enqueue_startup_job(
                f"feature.{feature}.post_show",
                generation,
                _create_feature,
                prerequisite=lambda: _startup_imports_ready(generation),
            )
        _enqueue_startup_job(
            "coordinator.construct",
            generation,
            lambda: _construct_coordinator(generation),
            prerequisite=lambda: _startup_imports_ready(generation),
        )
        _enqueue_startup_job(
            "coordinator.start",
            generation,
            _start_coordinator,
            prerequisite=lambda: coordinator_runtime is not None,
        )
        _enqueue_startup_job(
            "library.probe.start",
            generation,
            lambda: _start_library_probe(generation),
            prerequisite=lambda: coordinator_started,
        )

    def _continue_after_shell() -> None:
        startup_input_guard.release()
        try:
            _top_level_graphics_contract(
                window,
                selected_rhi_backend_name(),
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "Failed to inspect the post-paint graphics contract",
                exc_info=True,
            )
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
        _register_attempt_resources(generation)
        startup.transition(StartupPhase.INTERACTIVE, reason="retry")
        _initialize_features_after_show(generation)

    startup.startupDegraded.connect(
        lambda failure: window.show_startup_recovery(
            failure.message,
            details=(
                f"phase={failure.phase.value}; "
                f"code={failure.code}; "
                f"operation={failure.operation or 'unknown'}; "
                f"native_code={failure.native_code or 'unknown'}; "
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
        # The event loop can return before startup reaches a terminal phase
        # (for example, when an embedded host or a test double exits
        # immediately).  Cancel the attempt so its watchdog and registered
        # resources cannot fire against an already torn-down window.
        startup.cancel()
        startup_jobs.close()
        startup_imports.close()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
