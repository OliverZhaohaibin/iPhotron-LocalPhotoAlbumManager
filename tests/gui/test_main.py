from __future__ import annotations

import os
from pathlib import Path

from iPhoto.gui.main import _configure_qt_opengl_defaults, _prepare_qt_runtime_for_maps


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
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)

    _configure_qt_opengl_defaults()

    assert helper_calls == [True]
    assert len(attributes) == 2
    assert all(enabled is True for _, enabled in attributes)
    assert len(default_formats) == 1


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


def test_prepare_qt_runtime_for_maps_sets_xcb_glx_on_linux_when_native_widget_exists(monkeypatch) -> None:
    monkeypatch.setattr("iPhoto.gui.main.sys.platform", "linux")
    monkeypatch.setattr("iPhoto.gui.main._is_packaged_runtime", lambda: False)
    monkeypatch.setattr("maps.map_sources.has_usable_osmand_native_widget", lambda root: True)
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_OPENGL", raising=False)
    monkeypatch.delenv("QT_XCB_GL_INTEGRATION", raising=False)

    _prepare_qt_runtime_for_maps()

    assert os.environ["QT_QPA_PLATFORM"] == "xcb"
    assert os.environ["QT_OPENGL"] == "desktop"
    assert os.environ["QT_XCB_GL_INTEGRATION"] == "xcb_glx"


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


def test_prepare_qt_runtime_for_maps_forces_xcb_glx_in_packaged_linux_builds(monkeypatch) -> None:
    monkeypatch.setattr("iPhoto.gui.main.sys.platform", "linux")
    monkeypatch.setattr("iPhoto.gui.main._is_packaged_runtime", lambda: True)
    monkeypatch.delenv("IPHOTO_ALLOW_PACKAGED_LINUX_WAYLAND", raising=False)
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_OPENGL", raising=False)
    monkeypatch.delenv("QT_XCB_GL_INTEGRATION", raising=False)

    _prepare_qt_runtime_for_maps()

    assert os.environ["QT_QPA_PLATFORM"] == "xcb"
    assert os.environ["QT_OPENGL"] == "desktop"
    assert os.environ["QT_XCB_GL_INTEGRATION"] == "xcb_glx"


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


def test_main_applies_pending_map_extension_before_qt_setup(monkeypatch) -> None:
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
    monkeypatch.setattr("iPhoto.gui.main._configure_qt_opengl_defaults", lambda: call_order.append(("configure_gl", None)))
    monkeypatch.setattr("iPhoto.gui.main.QApplication", _FakeApp)
    monkeypatch.setattr("iPhoto.gui.main.QPalette", _FakePalette)
    monkeypatch.setattr("iPhoto.gui.main.QColor", _FakeColor)
    monkeypatch.setattr("iPhoto.gui.main.Qt", type("FakeQt", (), {"GlobalColor": type("GlobalColor", (), {"black": 0})(), "ApplicationAttribute": type("ApplicationAttribute", (), {})()}))
    monkeypatch.setattr("iPhoto.gui.main.QTimer.singleShot", lambda _delay, _callback: None)

    class _FakeRuntimeContext:
        @staticmethod
        def create(*, defer_startup: bool = False):
            call_order.append(("create_context", defer_startup))
            return type("FakeContext", (), {"resume_startup_tasks": lambda self: None})()

    class _FakeWindow:
        def __init__(self, _context):
            self.ui = type("FakeUi", (), {"sidebar": type("FakeSidebar", (), {"select_all_photos": lambda *a, **k: None})()})()

        def show(self) -> None:
            call_order.append(("show", None))

        def set_coordinator(self, _coordinator) -> None:
            return None

    class _FakeCoordinator:
        def __init__(self, _window, _context):
            return None

        def start(self) -> None:
            return None

    monkeypatch.setattr("iPhoto.utils.logging.get_logger", lambda: None)
    monkeypatch.setitem(__import__("sys").modules, "iPhoto.bootstrap.runtime_context", type("Mod", (), {"RuntimeContext": _FakeRuntimeContext})())
    monkeypatch.setitem(__import__("sys").modules, "iPhoto.gui.coordinators.main_coordinator", type("Mod", (), {"MainCoordinator": _FakeCoordinator})())
    monkeypatch.setitem(__import__("sys").modules, "iPhoto.gui.ui.main_window", type("Mod", (), {"MainWindow": _FakeWindow})())

    from iPhoto.gui.main import main

    main([])

    assert call_order[0][0] == "prefer"
    assert call_order[1][0] == "apply_pending"
    assert call_order[2][0] == "prepare_maps"
