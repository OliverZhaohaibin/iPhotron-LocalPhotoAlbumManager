from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QCloseEvent, QSurface, QSurfaceFormat

from iPhoto.gui.main import (
    _bootstrap_macos_external_tool_path,
    _configure_qt_opengl_defaults,
    _map_gl_surface_format,
    _prepare_qt_runtime_for_maps,
    _prepare_top_level_rhi_surface,
    _startup_feature_plan,
    _startup_timing_plan,
    _top_level_graphics_contract,
    _StartupInputGuard,
)
from iPhoto.gui.ui import main_window as main_window_module


def test_bootstrap_macos_external_tool_path_prepends_existing_paths_once(monkeypatch) -> None:
    existing_paths = {"/opt/homebrew/bin", "/usr/local/bin"}
    darwin_pathsep = ":"

    def fake_is_dir(path: Path) -> bool:
        return path.as_posix() in existing_paths

    monkeypatch.setattr("iPhoto.gui.main.sys.platform", "darwin")
    monkeypatch.setattr("iPhoto.gui.main.Path.is_dir", fake_is_dir)
    monkeypatch.setenv("PATH", "/usr/bin:/opt/homebrew/bin:/bin")

    _bootstrap_macos_external_tool_path()

    assert os.environ["PATH"].split(darwin_pathsep) == [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]

    _bootstrap_macos_external_tool_path()

    assert os.environ["PATH"].split(darwin_pathsep) == [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]


def test_main_window_close_event_runs_shutdown_once(monkeypatch, qapp) -> None:
    cleanup_calls: list[bool] = []
    shutdown_calls: list[bool] = []

    class _FakeSignal:
        def connect(self, _callback) -> None:
            return None

    class _FakeUi:
        featureCreated = _FakeSignal()

        def setupUi(self, _window, _library) -> None:
            return None

    class _FakeWindowManager:
        def __init__(self, _window, _ui) -> None:
            return None

        def cleanup(self) -> None:
            cleanup_calls.append(True)

        def set_detail_coordinator(self, _controller) -> None:
            return None

    monkeypatch.setattr(main_window_module, "Ui_MainWindow", _FakeUi)
    monkeypatch.setattr(main_window_module, "FramelessWindowManager", _FakeWindowManager)

    window = main_window_module.MainWindow(
        SimpleNamespace(library=object(), translation=None)
    )

    class _ReentrantCoordinator:
        def shutdown(self) -> None:
            shutdown_calls.append(True)
            window.closeEvent(QCloseEvent())

    window._coordinator_lifecycle = _ReentrantCoordinator()
    try:
        window.closeEvent(QCloseEvent())
        window.closeEvent(QCloseEvent())
    finally:
        # ``deleteLater`` alone leaves the fake MainWindow alive until some
        # unrelated test next pumps the application event queue.  Destroy it
        # while the monkeypatched collaborators are still installed so no
        # deferred resize/change event can leak into the following test.
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        qapp.processEvents()

    assert cleanup_calls == [True]
    assert shutdown_calls == [True]


def test_bootstrap_macos_external_tool_path_skips_non_macos(monkeypatch) -> None:
    def fail_if_called(_path: Path) -> bool:
        raise AssertionError("Path.is_dir should not be called off macOS")

    monkeypatch.setattr("iPhoto.gui.main.sys.platform", "linux")
    monkeypatch.setattr("iPhoto.gui.main.Path.is_dir", fail_if_called)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    _bootstrap_macos_external_tool_path()

    assert os.environ["PATH"] == "/usr/bin:/bin"


def test_configure_qt_opengl_defaults_routes_shader_cache_and_prefers_desktop_opengl(monkeypatch) -> None:
    helper_calls: list[bool] = []
    attributes: list[tuple[object, bool]] = []
    default_formats: list[object] = []

    monkeypatch.setattr(
        "iPhoto.gui.main.configure_shader_cache_environment",
        lambda: helper_calls.append(True),
    )
    monkeypatch.setattr(
        "iPhoto.gui.main.QApplication.setAttribute",
        lambda attr, enabled=True: attributes.append((attr, enabled)),
    )
    monkeypatch.setattr(
        "iPhoto.gui.main.QSurfaceFormat.setDefaultFormat",
        lambda fmt: default_formats.append(fmt),
    )
    monkeypatch.setattr("iPhoto.gui.render_backend.sys.platform", "linux")
    monkeypatch.setattr("iPhoto.gui.main.sys.platform", "linux")
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)
    monkeypatch.delenv("IPHOTO_RHI_BACKEND", raising=False)

    _configure_qt_opengl_defaults()

    assert helper_calls == [True]
    assert len(attributes) == 2
    assert all(enabled is True for _, enabled in attributes)
    assert (Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True) in attributes
    assert (Qt.ApplicationAttribute.AA_UseDesktopOpenGL, True) in attributes
    assert len(default_formats) == 1
    assert default_formats[0].depthBufferSize() == 24
    assert default_formats[0].stencilBufferSize() == 8
    assert default_formats[0].alphaBufferSize() == 0
    assert default_formats[0].samples() == 0


def test_configure_qt_opengl_defaults_keeps_map_gl_contexts_on_macos_auto(monkeypatch) -> None:
    helper_calls: list[bool] = []
    attributes: list[tuple[object, bool]] = []
    default_formats: list[object] = []

    monkeypatch.setattr(
        "iPhoto.gui.main.configure_shader_cache_environment",
        lambda: helper_calls.append(True),
    )
    monkeypatch.setattr(
        "iPhoto.gui.main.QApplication.setAttribute",
        lambda attr, enabled=True: attributes.append((attr, enabled)),
    )
    monkeypatch.setattr(
        "iPhoto.gui.main.QSurfaceFormat.setDefaultFormat",
        lambda fmt: default_formats.append(fmt),
    )
    monkeypatch.setattr("iPhoto.gui.render_backend.sys.platform", "darwin")
    monkeypatch.setattr("iPhoto.gui.main.sys.platform", "darwin")
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)
    monkeypatch.delenv("IPHOTO_RHI_BACKEND", raising=False)

    _configure_qt_opengl_defaults()

    assert helper_calls == [True]
    assert attributes == [(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)]
    assert len(default_formats) == 1
    assert default_formats[0].depthBufferSize() == 24
    assert default_formats[0].stencilBufferSize() == 8
    assert default_formats[0].alphaBufferSize() == 8
    assert default_formats[0].samples() == 0


@pytest.mark.parametrize(
    ("platform", "alpha_bits"),
    (("darwin", 8), ("win32", 8), ("linux", 0)),
)
def test_global_opengl_format_matches_top_level_translucency(
    platform: str,
    alpha_bits: int,
) -> None:
    assert _map_gl_surface_format(platform).alphaBufferSize() == alpha_bits


def test_configure_qt_opengl_defaults_still_routes_shader_cache_when_opengl_is_disabled(monkeypatch) -> None:
    helper_calls: list[bool] = []
    attributes: list[tuple[object, bool]] = []

    monkeypatch.setattr(
        "iPhoto.gui.main.configure_shader_cache_environment",
        lambda: helper_calls.append(True),
    )
    monkeypatch.setattr(
        "iPhoto.gui.main.QApplication.setAttribute",
        lambda attr, enabled=True: attributes.append((attr, enabled)),
    )
    monkeypatch.setattr(
        "iPhoto.gui.main.QSurfaceFormat.setDefaultFormat",
        lambda _fmt: None,
    )
    monkeypatch.setenv("IPHOTO_DISABLE_OPENGL", "1")

    _configure_qt_opengl_defaults()

    assert helper_calls == [True]
    assert attributes == []


def test_prepare_qt_runtime_for_maps_does_not_force_xcb_for_native_widget(monkeypatch) -> None:
    monkeypatch.setattr("iPhoto.gui.main.sys.platform", "linux")
    monkeypatch.setattr("iPhoto.gui.main._is_packaged_runtime", lambda: False)
    monkeypatch.setattr("maps.map_sources.has_usable_osmand_native_widget", lambda root: True)
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_OPENGL", raising=False)
    monkeypatch.delenv("QT_XCB_GL_INTEGRATION", raising=False)

    _prepare_qt_runtime_for_maps()

    assert "QT_QPA_PLATFORM" not in os.environ
    assert "QT_OPENGL" not in os.environ
    assert "QT_XCB_GL_INTEGRATION" not in os.environ


def test_prepare_qt_runtime_for_maps_skips_when_native_widget_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("iPhoto.gui.main.sys.platform", "linux")
    monkeypatch.setattr("iPhoto.gui.main._is_packaged_runtime", lambda: False)
    monkeypatch.setattr("maps.map_sources.has_usable_osmand_native_widget", lambda root: False)
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_OPENGL", raising=False)
    monkeypatch.delenv("QT_XCB_GL_INTEGRATION", raising=False)

    _prepare_qt_runtime_for_maps()

    assert "QT_QPA_PLATFORM" not in os.environ
    assert "QT_OPENGL" not in os.environ
    assert "QT_XCB_GL_INTEGRATION" not in os.environ


def test_prepare_qt_runtime_for_maps_preserves_wayland_in_packaged_linux_builds(monkeypatch) -> None:
    monkeypatch.setattr("iPhoto.gui.main.sys.platform", "linux")
    monkeypatch.setattr("iPhoto.gui.main._is_packaged_runtime", lambda: True)
    monkeypatch.delenv("IPHOTO_ALLOW_PACKAGED_LINUX_WAYLAND", raising=False)
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_OPENGL", raising=False)
    monkeypatch.delenv("QT_XCB_GL_INTEGRATION", raising=False)

    _prepare_qt_runtime_for_maps()

    assert "QT_QPA_PLATFORM" not in os.environ
    assert "QT_OPENGL" not in os.environ
    assert "QT_XCB_GL_INTEGRATION" not in os.environ


def test_prepare_qt_runtime_for_maps_allows_packaged_linux_wayland_opt_out(monkeypatch) -> None:
    monkeypatch.setattr("iPhoto.gui.main.sys.platform", "linux")
    monkeypatch.setattr("iPhoto.gui.main._is_packaged_runtime", lambda: True)
    monkeypatch.setenv("IPHOTO_ALLOW_PACKAGED_LINUX_WAYLAND", "1")
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_OPENGL", raising=False)
    monkeypatch.delenv("QT_XCB_GL_INTEGRATION", raising=False)

    _prepare_qt_runtime_for_maps()

    assert "QT_QPA_PLATFORM" not in os.environ
    assert "QT_OPENGL" not in os.environ
    assert "QT_XCB_GL_INTEGRATION" not in os.environ


@pytest.mark.parametrize(
    ("platform", "expected"),
    (
        ("win32", ((), ("detail",))),
        ("darwin", ((), ("detail",))),
        ("linux", ((), ("detail",))),
    ),
)
def test_startup_feature_plan_completes_detail_after_show_on_every_platform(
    platform: str,
    expected: tuple[tuple[str, ...], tuple[str, ...]],
) -> None:
    assert _startup_feature_plan(platform) == expected


@pytest.mark.parametrize(
    ("platform", "expected"),
    (
        ("linux", (0, 0, 0)),
        ("win32", (0, 0, 0)),
        ("darwin", (0, 0, 0)),
    ),
)
def test_startup_timing_plan_has_no_fixed_platform_delay(
    platform: str,
    expected: tuple[int, int, int],
) -> None:
    assert tuple(_startup_timing_plan(platform)) == expected


@pytest.mark.parametrize(
    ("backend", "surface_type"),
    (
        ("metal", QSurface.SurfaceType.MetalSurface),
        ("direct3d11", QSurface.SurfaceType.Direct3DSurface),
        ("d3d11", QSurface.SurfaceType.Direct3DSurface),
        ("opengl", QSurface.SurfaceType.OpenGLSurface),
    ),
)
def test_prepare_top_level_rhi_surface_only_verifies_precreated_handle(
    backend: str,
    surface_type: QSurface.SurfaceType,
) -> None:
    class _FakeHandle:
        def surfaceType(self):  # noqa: N802 - Qt API
            return surface_type

    class _FakeWindow:
        def windowHandle(self):  # noqa: N802 - Qt API
            return _FakeHandle()

        def isVisible(self) -> bool:  # noqa: N802 - Qt API
            return False

    assert _prepare_top_level_rhi_surface(_FakeWindow(), backend) == surface_type.name


def test_prepare_top_level_rhi_surface_rejects_late_raster_handle() -> None:
    class _RasterHandle:
        def surfaceType(self):  # noqa: N802 - Qt API
            return QSurface.SurfaceType.RasterSurface

    class _FakeWindow:
        def windowHandle(self):  # noqa: N802 - Qt API
            return _RasterHandle()

        def isVisible(self) -> bool:  # noqa: N802 - Qt API
            return False

    with pytest.raises(RuntimeError, match="RasterSurface; expected one of MetalSurface"):
        _prepare_top_level_rhi_surface(_FakeWindow(), "metal")


def test_prepare_top_level_rhi_surface_allows_platform_deferred_handle() -> None:
    class _FakeWindow:
        def windowHandle(self):  # noqa: N802 - Qt API
            return None

    assert (
        _prepare_top_level_rhi_surface(_FakeWindow(), "opengl")
        == "deferred:OpenGLSurface"
    )


def test_windows_graphics_contract_reports_actual_surface_alpha(caplog) -> None:
    actual_format = QSurfaceFormat()
    actual_format.setAlphaBufferSize(0)

    class _Handle:
        def surfaceType(self):  # noqa: N802
            return QSurface.SurfaceType.OpenGLSurface

        def format(self):
            return actual_format

    class _Window:
        def windowHandle(self):  # noqa: N802
            return _Handle()

        def testAttribute(self, attribute):  # noqa: N802
            return attribute == Qt.WidgetAttribute.WA_TranslucentBackground

        def windowFlags(self):  # noqa: N802
            return Qt.WindowType.FramelessWindowHint

    payload = _top_level_graphics_contract(
        _Window(),
        "opengl",
        platform="win32",
    )

    assert payload == {
        "backend": "opengl",
        "surface_type": "OpenGLSurface",
        "actual_alpha_bits": 0,
        "translucent": True,
        "frameless": True,
    }
    assert "has no confirmed alpha buffer" in caplog.text


def test_startup_input_guard_filters_only_window_startup_input() -> None:
    installed_filters: list[object] = []
    removed_filters: list[object] = []

    class _FakeApp:
        def installEventFilter(self, event_filter) -> None:  # noqa: N802 - Qt style
            installed_filters.append(event_filter)

        def removeEventFilter(self, event_filter) -> None:  # noqa: N802 - Qt style
            removed_filters.append(event_filter)

    class _FakeObject:
        def __init__(self, parent=None) -> None:
            self._parent = parent

        def parent(self):
            return self._parent

    class _FakeEvent:
        def __init__(self, event_type) -> None:
            self._event_type = event_type

        def type(self):
            return self._event_type

    window = _FakeObject()
    child = _FakeObject(window)
    external = _FakeObject()
    guard = _StartupInputGuard(window, _FakeApp())

    guard.install()

    assert installed_filters == [guard]
    assert guard.eventFilter(child, _FakeEvent(QEvent.Type.MouseButtonPress)) is True
    assert guard.eventFilter(child, _FakeEvent(QEvent.Type.Wheel)) is True
    assert guard.eventFilter(child, _FakeEvent(QEvent.Type.Paint)) is False
    assert guard.eventFilter(external, _FakeEvent(QEvent.Type.MouseButtonPress)) is False

    guard.release()

    assert removed_filters == [guard]
    assert guard.eventFilter(child, _FakeEvent(QEvent.Type.MouseButtonPress)) is False


def test_settings_initialization_failure_emits_one_failed_terminal(
    monkeypatch,
    qapp,
    tmp_path: Path,
) -> None:
    from iPhoto.bootstrap import startup_orchestrator as orchestrator_module
    from iPhoto.bootstrap.bootstrap_settings import BootstrapSettings
    from iPhoto.gui.main import main

    events: list[tuple[str, dict]] = []

    class _FakeApp:
        def __init__(self, _arguments) -> None:
            return None

        def platformName(self) -> str:  # noqa: N802 - Qt API
            return "offscreen"

    class _BrokenSettings:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("settings unavailable")

    monkeypatch.setattr("iPhoto.gui.main._prefer_local_source_tree", lambda: None)
    monkeypatch.setattr("iPhoto.gui.main._bootstrap_macos_external_tool_path", lambda: None)
    monkeypatch.setattr("iPhoto.gui.main._prepare_qt_runtime_for_maps", lambda: None)
    monkeypatch.setattr("iPhoto.gui.main._configure_qt_opengl_defaults", lambda: None)
    monkeypatch.setattr("iPhoto.gui.main._enable_startup_hang_diagnostics", lambda: None)
    monkeypatch.setattr("iPhoto.gui.main.QApplication", _FakeApp)
    monkeypatch.setattr("iPhoto.utils.logging.get_logger", lambda: None)
    monkeypatch.setattr(
        "iPhoto.bootstrap.bootstrap_settings.load_bootstrap_settings",
        lambda: BootstrapSettings(path=tmp_path / "settings.json"),
    )
    monkeypatch.setattr("iPhoto.settings.manager.SettingsManager", _BrokenSettings)
    monkeypatch.setattr(
        orchestrator_module,
        "mark",
        lambda stage, **details: events.append((stage, details)),
    )

    assert main([]) == 1
    terminals = [
        stage
        for stage, _details in events
        if stage in {"startup.completed", "startup.degraded", "startup.failed", "startup.cancelled"}
    ]
    assert terminals == ["startup.failed"]


@pytest.mark.parametrize("platform", ("win32", "linux", "darwin"))
@pytest.mark.parametrize("emit_startup_ready", (True, False))
def test_main_creates_required_features_in_platform_safe_order(
    monkeypatch,
    platform: str,
    emit_startup_ready: bool,
) -> None:
    call_order: list[str] = []
    profile_marks: list[str] = []
    delayed_callbacks = []
    fake_color_role = type(
        "ColorRole",
        (),
        {
            "Window": object(),
            "WindowText": object(),
            "ToolTipBase": object(),
            "ToolTipText": object(),
        },
    )

    class _FakeColor:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def isValid(self) -> bool:
            return True

        def setAlpha(self, _value: int) -> None:
            return None

        def lightness(self) -> int:
            return 255

    class _FakePalette:
        ColorRole = fake_color_role

        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def color(self, _role):
            return _FakeColor()

        def setColor(self, *_args, **_kwargs) -> None:
            return None

    class _FakeApp:
        def __init__(self, _args) -> None:
            return None

        def palette(self):
            return _FakePalette()

        def setPalette(self, *_args, **_kwargs) -> None:
            return None

        def exec(self) -> int:
            # Model the event loop processing delayed startup work before it
            # exits.  main() now cancels any still-pending startup attempt when
            # exec() returns, matching the real application lifecycle.
            for callback in list(delayed_callbacks):
                callback()
            return 0

    class _FakeSignal:
        def __init__(self) -> None:
            self._callback = None

        def connect(self, callback) -> None:
            self._callback = callback

        def emit(self, *args) -> None:
            assert self._callback is not None
            self._callback(*args)

    startup_ready_signal = _FakeSignal()

    class _FakeSurface:
        def render_backend_name(self) -> str:
            return "test"

    class _FakeDetailPage:
        def native_surfaces(self):
            return (_FakeSurface(), _FakeSurface(), _FakeSurface())

    detail_page = _FakeDetailPage()

    class _FakeUi:
        sidebar = type(
            "FakeSidebar",
            (),
            {
                "select_all_photos": lambda *args, **kwargs: (
                    call_order.append("select"),
                    startup_ready_signal.emit() if emit_startup_ready else None,
                )
            },
        )()

        def __init__(self) -> None:
            self._prepared_detail_page = self.prepare_detail_native_hierarchy()

        def prepare_detail_native_hierarchy(self):
            call_order.append("prepare:detail")
            return detail_page

        def ensure_feature(self, feature: str):
            call_order.append(f"feature:{feature}")
            return detail_page

    class _FakeWindow:
        def __init__(self, _context) -> None:
            self.ui = _FakeUi()
            self.firstPainted = _FakeSignal()

        def show(self) -> None:
            assert profile_marks[-1] == "startup.show"
            call_order.append("show")
            self.firstPainted.emit()

        def bind_coordinators(self, _lifecycle, _gallery, _detail) -> None:
            call_order.append("set_coordinator")

    class _FakeRuntimeContext:
        @staticmethod
        def create(*, defer_startup: bool = False, settings=None):
            return type(
                "FakeContext",
                (),
                {
                    "request_startup_library_probe": (
                        lambda self: SimpleNamespace(request_id="startup-request")
                    ),
                    "commit_prepared_library": (
                        lambda self, prepared, *, defer_scan=False: call_order.append(
                            f"commit:{prepared.request_id}:{defer_scan}"
                        )
                    ),
                    "resume_startup_tasks": (
                        lambda self, *, defer_scan=False: call_order.append(
                            f"resume:{defer_scan}"
                        )
                    ),
                    "start_deferred_startup_scan": (
                        lambda self: call_order.append("scan")
                    ),
                },
            )()

    class _FakeProbeController:
        def __init__(self, _parent=None) -> None:
            self.ready = _FakeSignal()
            self.failed = _FakeSignal()

        def start(self, request) -> None:
            call_order.append(f"probe:{request.request_id}")
            self.ready.emit(
                SimpleNamespace(
                    request_id=request.request_id,
                    warnings=(),
                )
            )

        def cancel(self) -> None:
            call_order.append("probe:cancel")

    class _FakeCoordinator:
        def __init__(self, _window, _context) -> None:
            call_order.append("coordinator:create")
            self.gallery = SimpleNamespace(
                startup_model=self._startup_model,
                open_album_from_path=lambda _path: None,
            )
            self.detail = object()

        def start(self) -> None:
            call_order.append("coordinator:start")

        def _startup_model(self):
            return type(
                "FakeStartupModel",
                (),
                {
                    "startupGalleryReady": startup_ready_signal,
                    "begin_startup_gallery_warmup": (
                        lambda self: call_order.append("warmup")
                    ),
                },
            )()

    class _FakeStartupInputGuard:
        def __init__(self, _window, _app) -> None:
            self._active = False

        def install(self) -> None:
            self._active = True
            call_order.append("guard:install")

        def release(self) -> None:
            if not self._active:
                return
            self._active = False
            call_order.append("guard:release")

    monkeypatch.setattr("iPhoto.gui.main.sys.platform", platform)
    monkeypatch.setattr(
        "iPhoto.gui.main.mark",
        lambda stage, **_details: profile_marks.append(stage),
    )
    monkeypatch.setattr("iPhoto.gui.main._prefer_local_source_tree", lambda: None)
    monkeypatch.setattr("iPhoto.gui.main._prepare_qt_runtime_for_maps", lambda: None)
    monkeypatch.setattr("iPhoto.gui.main._configure_qt_opengl_defaults", lambda _root=None: None)
    monkeypatch.setattr("iPhoto.gui.main.QApplication", _FakeApp)
    monkeypatch.setattr("iPhoto.gui.main.QPalette", _FakePalette)
    monkeypatch.setattr("iPhoto.gui.main.QColor", _FakeColor)
    monkeypatch.setattr(
        "iPhoto.gui.main.Qt",
        type(
            "FakeQt",
            (),
            {
                "GlobalColor": type("GlobalColor", (), {"black": 0})(),
                "ApplicationAttribute": type("ApplicationAttribute", (), {})(),
            },
        ),
    )
    def _single_shot(delay, callback) -> None:
        if delay == 0:
            callback()
            return
        delayed_callbacks.append(callback)

    monkeypatch.setattr("iPhoto.gui.main.QTimer.singleShot", _single_shot)
    monkeypatch.setattr("iPhoto.gui.main._StartupInputGuard", _FakeStartupInputGuard)
    monkeypatch.setattr(
        "iPhoto.settings.manager.SettingsManager",
        lambda: type(
            "FakeSettings",
            (),
            {"load": lambda self: None, "get": lambda self, *_args: None},
        )(),
    )
    monkeypatch.setattr("iPhoto.utils.logging.get_logger", lambda: None)
    monkeypatch.setitem(
        __import__("sys").modules,
        "iPhoto.bootstrap.runtime_context",
        type("Mod", (), {"RuntimeContext": _FakeRuntimeContext})(),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "iPhoto.bootstrap.library_probe",
        type("Mod", (), {"LibraryProbeController": _FakeProbeController})(),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "iPhoto.gui.coordinators.desktop_coordinator_runtime",
        type("Mod", (), {"DesktopCoordinatorRuntime": _FakeCoordinator})(),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "iPhoto.gui.ui.main_window",
        type("Mod", (), {"MainWindow": _FakeWindow})(),
    )

    from iPhoto.gui.main import main

    assert main([]) == 0

    detail_index = call_order.index("feature:detail")
    prepare_index = call_order.index("prepare:detail")
    show_index = call_order.index("show")
    coordinator_index = call_order.index("coordinator:create")

    assert prepare_index < show_index < detail_index < coordinator_index
    assert "detail.native_hierarchy.before_verify" in profile_marks
    assert "detail.native_hierarchy.prepared" in profile_marks
    assert "detail.feature.completed" in profile_marks
    assert "windows_detail.before_create" not in profile_marks
    assert "windows_detail.created" not in profile_marks
    assert "feature:preview" not in call_order
    assert "feature:people" not in call_order
    # The shell becomes interactive immediately after the first-paint/watchdog
    # boundary; library probing and hidden feature creation must not retain the
    # global input filter.
    assert call_order.index("guard:release") < call_order.index("probe:startup-request")
    assert call_order.index("probe:startup-request") < call_order.index(
        "commit:startup-request:True"
    )
    assert call_order.index("commit:startup-request:True") < call_order.index("select")
    assert call_order.index("guard:release") < call_order.index("select")
    assert call_order.index("warmup") < call_order.index("select")
    assert len(delayed_callbacks) == 1
    for callback in list(delayed_callbacks):
        callback()
    assert call_order.index("select") < call_order.index("scan")
    assert call_order.count("scan") == 1


def test_main_defers_pending_map_extension_until_map_feature(monkeypatch) -> None:
    call_order: list[tuple[str, object]] = []
    fake_color_role = type("ColorRole", (), {"Window": object(), "WindowText": object(), "ToolTipBase": object(), "ToolTipText": object()})

    class _FakeColor:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def isValid(self) -> bool:
            return True

        def setAlpha(self, _value: int) -> None:
            return None

        def lightness(self) -> int:
            return 255

    class _FakePalette:
        ColorRole = fake_color_role

        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def color(self, _role):
            return _FakeColor()

        def setColor(self, *_args, **_kwargs) -> None:
            return None

    class _FakeApp:
        def __init__(self, _args) -> None:
            return None

        def palette(self):
            return _FakePalette()

        def setPalette(self, *_args, **_kwargs) -> None:
            return None

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(
        "maps.map_sources.apply_pending_osmand_extension_install",
        lambda root: call_order.append(("apply_pending", Path(root))),
    )
    monkeypatch.setattr("iPhoto.gui.main._prefer_local_source_tree", lambda: call_order.append(("prefer", None)))
    monkeypatch.setattr("iPhoto.gui.main._prepare_qt_runtime_for_maps", lambda: call_order.append(("prepare_maps", None)))
    monkeypatch.setattr(
        "iPhoto.gui.main._configure_qt_opengl_defaults",
        lambda _library_root=None: call_order.append(("configure_gl", None)),
    )
    monkeypatch.setattr(
        "iPhoto.gui.main._startup_feature_plan",
        lambda: ((), ()),
    )
    monkeypatch.setattr("iPhoto.gui.main.QApplication", _FakeApp)
    monkeypatch.setattr("iPhoto.gui.main.QPalette", _FakePalette)
    monkeypatch.setattr("iPhoto.gui.main.QColor", _FakeColor)
    monkeypatch.setattr("iPhoto.gui.main.Qt", type("FakeQt", (), {"GlobalColor": type("GlobalColor", (), {"black": 0})(), "ApplicationAttribute": type("ApplicationAttribute", (), {})()}))
    monkeypatch.setattr("iPhoto.gui.main.QTimer.singleShot", lambda _delay, _callback: None)
    monkeypatch.setattr(
        "iPhoto.settings.manager.SettingsManager",
        lambda: type(
            "FakeSettings",
            (),
            {"load": lambda self: None, "get": lambda self, *_args: None},
        )(),
    )

    class _FakeRuntimeContext:
        @staticmethod
        def create(*, defer_startup: bool = False, settings=None):
            call_order.append(("create_context", defer_startup))
            return type("FakeContext", (), {"resume_startup_tasks": lambda self: None})()

    class _FakeWindow:
        def __init__(self, _context):
            class _FakeUi:
                sidebar = type(
                    "FakeSidebar",
                    (),
                    {"select_all_photos": lambda *args, **kwargs: None},
                )()

                def __init__(self) -> None:
                    self._prepared_detail_page = self.prepare_detail_native_hierarchy()

                def ensure_feature(self, feature: str) -> None:
                    call_order.append(("ensure_feature", feature))

                def prepare_detail_native_hierarchy(self):
                    call_order.append(("prepare_detail_native_hierarchy", None))
                    return type(
                        "FakeDetailPage",
                        (),
                        {"native_surfaces": lambda self: ()},
                    )()

            self.ui = _FakeUi()
            self.firstPainted = type("FakeSignal", (), {"connect": lambda *a, **k: None})()

        def show(self) -> None:
            call_order.append(("show", None))

        def bind_coordinators(self, _lifecycle, _gallery, _detail) -> None:
            return None

    class _FakeCoordinator:
        def __init__(self, _window, _context):
            self.gallery = SimpleNamespace(startup_model=lambda: None)
            self.detail = object()

        def start(self) -> None:
            return None

    monkeypatch.setattr("iPhoto.utils.logging.get_logger", lambda: None)
    monkeypatch.setitem(__import__("sys").modules, "iPhoto.bootstrap.runtime_context", type("Mod", (), {"RuntimeContext": _FakeRuntimeContext})())
    monkeypatch.setitem(__import__("sys").modules, "iPhoto.gui.coordinators.desktop_coordinator_runtime", type("Mod", (), {"DesktopCoordinatorRuntime": _FakeCoordinator})())
    monkeypatch.setitem(__import__("sys").modules, "iPhoto.gui.ui.main_window", type("Mod", (), {"MainWindow": _FakeWindow})())

    from iPhoto.gui.main import main

    main([])

    assert call_order[0][0] == "prefer"
    assert not any(name == "apply_pending" for name, _value in call_order)
    assert ("prepare_detail_native_hierarchy", None) in call_order
    assert call_order[1][0] == "prepare_maps"
