"""GUI entry point for the iPhoto desktop application."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import NamedTuple

from iPhoto.bootstrap.startup_profile import mark

mark("module.before_qt_imports")
from PySide6.QtCore import QEvent, QObject, QTimer, Qt  # noqa: E402, I001
from PySide6.QtGui import QColor, QPalette, QSurfaceFormat  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from iPhoto.bootstrap.qt_shader_cache import configure_shader_cache_environment  # noqa: E402
from iPhoto.gui.render_backend import should_configure_global_desktop_opengl  # noqa: E402

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
_LINUX_FIRST_POST_PAINT_DELAY_MS = 120
_LINUX_POST_SHOW_FEATURE_INTERVAL_MS = 50
_LINUX_COORDINATOR_READY_DELAY_MS = 100
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
        configure_shader_cache_environment()
    else:
        configure_shader_cache_environment(library_root=library_root)


def _opengl_explicitly_disabled() -> bool:
    """Return whether all OpenGL-backed UI surfaces should be disabled."""

    return os.environ.get("IPHOTO_DISABLE_OPENGL", "").strip().lower() in _TRUE_ENV_VALUES


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
    """Apply Linux Qt platform flags required by the native OsmAnd widget.

    ``PhotoMapView`` prefers the native OsmAnd widget when its runtime is
    available. That widget expects Qt to use the XCB/GLX desktop OpenGL path on
    Linux; without these flags the application can start successfully and only
    fail later when the map view is opened with GLEW reporting missing GLX
    support.
    """

    if sys.platform != "linux":
        return

    if _opengl_explicitly_disabled():
        return

    if _is_packaged_runtime():
        if _allow_packaged_linux_wayland():
            return
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    else:
        try:
            from maps.map_sources import (
                has_usable_osmand_native_widget,
                prefer_osmand_native_widget,
            )
        except Exception:  # noqa: BLE001
            return

        maps_package_root = Path(__file__).resolve().parents[2] / "maps"
        if not prefer_osmand_native_widget() or not has_usable_osmand_native_widget(
            maps_package_root
        ):
            return

    if not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
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
    deferred = ("detail", "preview", "people")
    if target_platform in {"win32", "linux"}:
        return (("detail",), ("preview", "people"))
    return ((), deferred)


def _startup_timing_plan(platform: str | None = None) -> _StartupTimingPlan:
    """Return post-paint startup delays for the target platform."""

    target_platform = sys.platform if platform is None else platform
    if target_platform == "linux":
        return _StartupTimingPlan(
            _LINUX_FIRST_POST_PAINT_DELAY_MS,
            _LINUX_POST_SHOW_FEATURE_INTERVAL_MS,
            _LINUX_COORDINATOR_READY_DELAY_MS,
        )
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

    arguments = list(sys.argv if argv is None else argv)
    from iPhoto.settings.manager import SettingsManager

    startup_settings = SettingsManager()
    startup_settings.load()
    mark("settings.loaded")
    saved_library = startup_settings.get("basic_library_path")
    saved_library_root = (
        Path(saved_library).expanduser()
        if isinstance(saved_library, str) and saved_library
        else None
    )
    _prepare_qt_runtime_for_maps()
    _configure_qt_opengl_defaults(saved_library_root)
    mark("qapplication.before_create")
    app = QApplication(arguments)
    mark("qapplication.created")

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
    startup_input_guard = _StartupInputGuard(window, app)
    startup_input_guard.install()

    pre_show_features, post_show_features = _startup_feature_plan()
    startup_timing = _startup_timing_plan()
    for feature in pre_show_features:
        if feature == "detail":
            mark("rhi_detail.before_create")
        window.ui.ensure_feature(feature)
        if feature == "detail":
            mark("rhi_detail.created")

    # Coordinator needs Window, Context, and Container
    def _initialize_after_show() -> None:
        try:
            mark("post_paint.begin")
            # Importing the coordinator expands the controller/view-model graph;
            # keep that work behind the OS-confirmed first paint.
            from iPhoto.gui.coordinators.main_coordinator import MainCoordinator

            mark("main_coordinator.imported")
            _logger.info("_initialize_after_show: creating MainCoordinator")
            coordinator = MainCoordinator(window, context)
            window.set_coordinator(coordinator)
            coordinator.start()
            mark("main_coordinator.started")

            def _resume_startup_tasks() -> None:
                try:
                    _logger.info(
                        "_initialize_after_show: coordinator started, resuming startup tasks"
                    )
                    context.resume_startup_tasks()

                    if len(arguments) > 1:
                        _logger.info(
                            "_initialize_after_show: opening album from CLI argument %s",
                            arguments[1],
                        )
                        coordinator.open_album_from_path(Path(arguments[1]))
                        return
                    _logger.info("_initialize_after_show: selecting All Photos in sidebar")
                    window.ui.sidebar.select_all_photos(emit_signal=True)
                finally:
                    startup_input_guard.release()

            QTimer.singleShot(
                startup_timing.coordinator_ready_delay_ms,
                _resume_startup_tasks,
            )
        except Exception:
            startup_input_guard.release()
            raise

    def _initialize_features_after_show() -> None:
        # QWidget creation must remain on the GUI thread. Splitting hidden
        # feature construction across event-loop turns keeps the newly painted
        # window responsive while preserving the current coordinator contract.
        pending = iter(post_show_features)

        def _create_next() -> None:
            try:
                try:
                    feature = next(pending)
                except StopIteration:
                    QTimer.singleShot(0, _initialize_after_show)
                    return
                mark("feature.before_create", feature=feature)
                window.ui.ensure_feature(feature)
                mark("feature.created", feature=feature)
                QTimer.singleShot(startup_timing.feature_interval_ms, _create_next)
            except Exception:
                startup_input_guard.release()
                raise

        _create_next()

    window.firstPainted.connect(
        lambda: QTimer.singleShot(
            startup_timing.first_post_paint_delay_ms,
            _initialize_features_after_show,
        )
    )
    window.show()
    mark("main_window.show_called")

    return app.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
