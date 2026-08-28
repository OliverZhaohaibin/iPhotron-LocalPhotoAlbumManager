from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QShader
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


def test_auto_keeps_opengl_on_windows_during_staged_migration(monkeypatch) -> None:
    monkeypatch.setattr(render_backend.sys, "platform", "win32")
    monkeypatch.delenv("IPHOTO_RHI_BACKEND", raising=False)

    assert render_backend.select_qrhi_widget_api() == QRhiWidget.Api.OpenGL


def test_windows_override_can_select_d3d11(monkeypatch) -> None:
    monkeypatch.setattr(render_backend.sys, "platform", "win32")
    monkeypatch.setenv("IPHOTO_RHI_BACKEND", "d3d11")

    expected = getattr(QRhiWidget.Api, "Direct3D11", QRhiWidget.Api.OpenGL)
    assert render_backend.select_qrhi_widget_api() == expected


def test_d3d11_override_falls_back_to_opengl_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(render_backend.sys, "platform", "linux")
    monkeypatch.setenv("IPHOTO_RHI_BACKEND", "d3d11")

    assert render_backend.select_qrhi_widget_api() == QRhiWidget.Api.OpenGL


def test_backend_override_can_force_opengl_on_macos(monkeypatch) -> None:
    monkeypatch.setattr(render_backend.sys, "platform", "darwin")
    monkeypatch.setenv("IPHOTO_RHI_BACKEND", "opengl")

    assert render_backend.select_qrhi_widget_api() == QRhiWidget.Api.OpenGL


def test_media_qsb_assets_include_hlsl_50_for_d3d11() -> None:
    shader_dir = Path(render_backend.__file__).parent / "ui" / "widgets"
    shader_names = (
        "image_viewer_rhi.vert.qsb",
        "image_viewer_rhi.frag.qsb",
        "image_viewer_overlay.vert.qsb",
        "image_viewer_overlay.frag.qsb",
        "video_renderer.vert.qsb",
        "video_renderer.frag.qsb",
    )

    for shader_name in shader_names:
        shader = QShader.fromSerialized((shader_dir / shader_name).read_bytes())
        hlsl_versions = {
            key.sourceVersion().version()
            for key in shader.availableShaders()
            if key.source() == QShader.Source.HlslShader
        }
        assert 50 in hlsl_versions, shader_name
