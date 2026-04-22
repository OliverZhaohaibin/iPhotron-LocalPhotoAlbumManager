from __future__ import annotations

import os

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
    monkeypatch.setattr("maps.map_sources.has_usable_osmand_native_widget", lambda root: False)
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_OPENGL", raising=False)
    monkeypatch.delenv("QT_XCB_GL_INTEGRATION", raising=False)

    _prepare_qt_runtime_for_maps()

    assert "QT_QPA_PLATFORM" not in os.environ
    assert "QT_OPENGL" not in os.environ
    assert "QT_XCB_GL_INTEGRATION" not in os.environ
