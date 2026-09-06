"""Tests for GL image texture resource tracking."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtGui", reason="Qt GUI not available", exc_type=ImportError)

from PySide6.QtGui import QImage

from iPhoto.gui.ui.widgets.gl_image_viewer.resources import TextureResourceManager


class _RendererStub:
    def __init__(self) -> None:
        self.upload_calls = 0
        self.delete_calls = 0
        self._has_texture = False
        self.still_uploads: list[object] = []
        self.resident: set[object] = set()
        self.active: object | None = None

    def has_texture(self) -> bool:
        return self._has_texture

    def upload_texture(self, image: QImage) -> None:
        assert not image.isNull()
        self.upload_calls += 1
        self._has_texture = True

    def delete_texture(self) -> None:
        self.delete_calls += 1
        self._has_texture = False

    def upload_still_texture(self, key: object, image: QImage) -> None:
        assert not image.isNull()
        self.still_uploads.append(key)
        self.resident.add(key)
        self.active = key
        self._has_texture = True

    def has_still_texture(self, key: object) -> bool:
        return key in self.resident

    def activate_still_texture(self, key: object) -> bool:
        if key not in self.resident:
            return False
        self.active = key
        self._has_texture = True
        return True

    def clear_still_residency(self) -> None:
        self.resident.clear()
        self.active = None
        self._has_texture = False

    def trim_still_residency(self) -> None:
        self.resident = {self.active} if self.active is not None else set()


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
    assert renderer.still_uploads == ["asset://still"]

    manager.set_image(image, "asset://still", force_upload=True)
    assert manager.needs_texture_upload() is True
    assert manager.upload_texture_if_needed(image) is True
    assert renderer.still_uploads == ["asset://still", "asset://still"]


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


def test_stable_still_key_uploads_once_and_then_activates_resident_texture() -> None:
    renderer = _RendererStub()
    manager = _manager(renderer)
    image = QImage(32, 24, QImage.Format.Format_RGBA8888)
    image.fill(0xFF556677)

    manager.set_image(image, "still-a")
    assert manager.upload_texture_if_needed(image) is True
    assert renderer.still_uploads == ["still-a"]
    assert manager.has_resident_texture("still-a") is True
    assert manager.activate_resident_texture("still-a") is True
    assert renderer.still_uploads == ["still-a"]


def test_residency_deletion_runs_with_the_render_context_current() -> None:
    events: list[str] = []

    class _ContextRenderer(_RendererStub):
        def clear_still_residency(self) -> None:
            events.append("clear")
            super().clear_still_residency()

        def trim_still_residency(self) -> None:
            events.append("trim")
            super().trim_still_residency()

    renderer = _ContextRenderer()
    manager = TextureResourceManager(
        renderer_provider=lambda: renderer,
        context_provider=lambda: object(),
        make_current=lambda: events.append("make-current"),
        done_current=lambda: events.append("done-current"),
    )

    manager.clear_still_residency()
    manager.trim_still_residency()

    assert events == [
        "make-current",
        "clear",
        "done-current",
        "make-current",
        "trim",
        "done-current",
    ]
