from __future__ import annotations

from PySide6.QtWidgets import QRhiWidget

from iPhoto.gui import render_backend


def test_auto_selects_metal_on_macos_when_available(monkeypatch) -> None:
    monkeypatch.setattr(render_backend.sys, "platform", "darwin")
    monkeypatch.delenv("IPHOTO_RHI_BACKEND", raising=False)

    expected = getattr(QRhiWidget.Api, "Metal", QRhiWidget.Api.OpenGL)

    assert render_backend.select_qrhi_widget_api() == expected


def test_auto_keeps_opengl_on_linux(monkeypatch) -> None:
    monkeypatch.setattr(render_backend.sys, "platform", "linux")
    monkeypatch.delenv("IPHOTO_RHI_BACKEND", raising=False)

    assert render_backend.select_qrhi_widget_api() == QRhiWidget.Api.OpenGL


def test_backend_override_can_force_opengl_on_macos(monkeypatch) -> None:
    monkeypatch.setattr(render_backend.sys, "platform", "darwin")
    monkeypatch.setenv("IPHOTO_RHI_BACKEND", "opengl")

    assert render_backend.select_qrhi_widget_api() == QRhiWidget.Api.OpenGL
