from __future__ import annotations

from iPhoto.gui.main import _configure_qt_opengl_defaults


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
