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


def test_failed_gl_allocation_preserves_active_texture_and_recovers(mocker) -> None:
    _mock_gl_uploads(mocker)
    manager = TextureManager()
    manager.upload_still_texture("current", _image(8, 8))
    active_texture = manager._texture_id
    gl.glGenTextures.side_effect = [0, 42]

    manager.upload_still_texture("failed", _image(9, 8))

    failure = manager.take_still_upload_result()
    assert failure is not None and failure["success"] is False
    assert manager._active_still_key == "current"
    assert manager._texture_id == active_texture
    assert not manager.has_still_texture("failed")

    manager.upload_still_texture("recovered", _image(10, 8))

    assert manager.has_still_texture("recovered")
    assert manager._active_still_key == "recovered"


def test_gl_prefetch_never_evicts_visible_texture_for_budget(mocker) -> None:
    _mock_gl_uploads(mocker)
    manager = TextureManager()
    current = _image(8, 8)
    manager.upload_still_texture("current", current)
    active_texture = manager._texture_id
    manager._still_budget_bytes = current.sizeInBytes()

    assert manager.warm_still_texture("next", _image(9, 8)) is False

    assert manager._active_still_key == "current"
    assert manager._texture_id == active_texture
    assert manager.has_still_texture("current")
    assert not manager.has_still_texture("next")


def test_gl_foreground_never_overwrites_same_size_visible_texture(mocker) -> None:
    _mock_gl_uploads(mocker)
    manager = TextureManager()
    current = _image(8, 8)
    manager.upload_still_texture("current", current)
    active_texture = manager._texture_id
    manager._still_budget_bytes = current.sizeInBytes()
    upload_count = gl.glTexSubImage2D.call_count

    manager.upload_still_texture("replacement", _image(8, 8))

    assert gl.glTexSubImage2D.call_count == upload_count
    assert manager._active_still_key == "current"
    assert manager._texture_id == active_texture
    assert manager.has_still_texture("current")
    assert not manager.has_still_texture("replacement")
    result = manager.take_still_upload_result()
    assert result is not None and result["reason"] == "residency_budget"


def test_gl_failed_reused_neighbor_is_removed_from_residency(mocker) -> None:
    _mock_gl_uploads(mocker)
    manager = TextureManager()
    image = _image(8, 8)
    manager._still_budget_bytes = image.sizeInBytes() * 2
    manager.upload_still_texture("current", image)
    manager.upload_still_texture("neighbor", image)
    manager.activate_still_texture("current")
    active_texture = manager._texture_id
    neighbor_texture = manager._still_textures["neighbor"][0]
    gl.glGetError.reset_mock()
    gl.glGetError.side_effect = [gl.GL_NO_ERROR, gl.GL_OUT_OF_MEMORY]

    manager.upload_still_texture("replacement", image)

    assert tuple(manager._still_textures) == ("current",)
    assert manager._active_still_key == "current"
    assert manager._texture_id == active_texture
    assert not manager.has_still_texture("neighbor")
    assert not manager.has_still_texture("replacement")
    deleted_ids = [int(call.args[1][0]) for call in gl.glDeleteTextures.call_args_list]
    assert neighbor_texture in deleted_ids
    result = manager.take_still_upload_result()
    assert result is not None and result["reason"] == "upload_failed"
