from __future__ import annotations

from PySide6.QtGui import QImage

from iPhoto.gui.ui.widgets.gl_texture_manager import TextureManager, gl


def _image(width: int = 8, height: int = 8) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(0xFF123456)
    return image


def _mock_gl_uploads(mocker):
    ids = iter(range(1, 20))
    mocker.patch.object(gl, "glGenTextures", side_effect=lambda _count: next(ids))
    for name in (
        "glBindTexture",
        "glTexImage2D",
        "glTexParameteri",
        "glPixelStorei",
        "glTexSubImage2D",
        "glDeleteTextures",
        "glGenerateMipmap",
    ):
        mocker.patch.object(gl, name)
    mocker.patch.object(gl, "glGetError", return_value=gl.GL_NO_ERROR)


def test_still_upload_does_not_allocate_or_generate_mipmaps(mocker) -> None:
    _mock_gl_uploads(mocker)
    manager = TextureManager()

    manager.upload_still_texture("current", _image())

    assert manager.has_still_texture("current")
    assert manager._texture_uses_mipmaps is False
    gl.glGenerateMipmap.assert_not_called()
    min_filter_calls = [
        call for call in gl.glTexParameteri.call_args_list
        if call.args[1] == gl.GL_TEXTURE_MIN_FILTER
    ]
    assert min_filter_calls[-1].args[2] == gl.GL_LINEAR


def test_still_residency_keeps_three_entries_and_reuses_activation_without_upload(mocker) -> None:
    _mock_gl_uploads(mocker)
    manager = TextureManager()
    image = _image()
    for key in ("previous", "current", "next"):
        manager.upload_still_texture(key, image)
    upload_count = gl.glTexSubImage2D.call_count

    assert manager.activate_still_texture("current")
    assert gl.glTexSubImage2D.call_count == upload_count
    manager.upload_still_texture("fourth", image)

    assert len(manager._still_textures) == 3
    assert manager.has_still_texture("fourth")
    assert not manager.has_still_texture("previous")


def test_still_residency_honours_byte_budget_without_evicting_active(mocker) -> None:
    _mock_gl_uploads(mocker)
    manager = TextureManager()
    manager._still_budget_bytes = _image().sizeInBytes() * 2

    manager.upload_still_texture("one", _image())
    manager.upload_still_texture("two", _image())
    manager.upload_still_texture("current", _image())

    assert manager.has_still_texture("current")
    assert sum(entry[3] for entry in manager._still_textures.values()) <= manager._still_budget_bytes


def test_different_sized_texture_evicts_before_allocating_replacement(mocker) -> None:
    _mock_gl_uploads(mocker)
    manager = TextureManager()
    manager.upload_still_texture("previous", _image(8, 8))
    manager.upload_still_texture("neighbor", _image(9, 8))
    manager.upload_still_texture("current", _image(10, 8))
    resident_keys_at_allocation: list[tuple[object, ...]] = []

    def _allocate(_count: int) -> int:
        resident_keys_at_allocation.append(tuple(manager._still_textures))
        return 99

    gl.glGenTextures.side_effect = _allocate
    manager.upload_still_texture("next", _image(11, 8))

    assert resident_keys_at_allocation == [("neighbor", "current")]
    gl.glDeleteTextures.assert_called()


def test_warming_a_resident_neighbor_refreshes_its_lru_position(mocker) -> None:
    _mock_gl_uploads(mocker)
    manager = TextureManager()
    image = _image()
    for key in ("stale", "previous", "current"):
        manager.upload_still_texture(key, image)

    assert manager.warm_still_texture("previous", image) is False
    manager.upload_still_texture("next", image)

    assert manager.has_still_texture("previous")
    assert manager.has_still_texture("next")
    assert not manager.has_still_texture("stale")


def test_rgba_video_uploads_keep_mipmaps_while_still_surfaces_do_not(mocker) -> None:
    _mock_gl_uploads(mocker)
    manager = TextureManager()

    manager.upload_still_texture("still", _image())
    assert manager.texture_uses_mipmaps() is False

    manager.upload_texture(_image())
    assert manager.texture_uses_mipmaps() is True
