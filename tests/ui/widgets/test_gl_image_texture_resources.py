"""Tests for GL image texture resource tracking."""

from __future__ import annotations

from PySide6.QtGui import QImage

from iPhoto.gui.ui.widgets.gl_image_viewer.resources import TextureResourceManager


class _RendererStub:
    def __init__(self) -> None:
        self.upload_calls = 0
        self.delete_calls = 0
        self._has_texture = False

    def has_texture(self) -> bool:
        return self._has_texture

    def upload_texture(self, image: QImage) -> None:
        assert not image.isNull()
        self.upload_calls += 1
        self._has_texture = True

    def delete_texture(self) -> None:
        self.delete_calls += 1
        self._has_texture = False


def _manager(renderer: _RendererStub) -> TextureResourceManager:
    return TextureResourceManager(
        renderer_provider=lambda: renderer,
        context_provider=lambda: object(),
        make_current=lambda: None,
        done_current=lambda: None,
    )


def test_force_upload_marks_existing_texture_dirty() -> None:
    renderer = _RendererStub()
    manager = _manager(renderer)
    image = QImage(64, 48, QImage.Format.Format_RGBA8888)
    image.fill(0xFF223344)

    manager.set_image(image, "asset://still")
    assert manager.needs_texture_upload() is True
    assert manager.upload_texture_if_needed(image) is True
    assert renderer.upload_calls == 1

    manager.set_image(image, "asset://still", force_upload=True)
    assert manager.needs_texture_upload() is True
    assert manager.upload_texture_if_needed(image) is True
    assert renderer.upload_calls == 2


def test_video_frames_without_stable_source_stay_dirty() -> None:
    renderer = _RendererStub()
    manager = _manager(renderer)
    image = QImage(32, 24, QImage.Format.Format_RGBA8888)
    image.fill(0xFF556677)

    manager.set_image(image, None)
    assert manager.needs_texture_upload() is True
    manager.upload_texture_if_needed(image)
    assert renderer.upload_calls == 1

    manager.set_image(image, None)
    assert manager.needs_texture_upload() is True
