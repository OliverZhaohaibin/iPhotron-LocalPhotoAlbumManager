from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage

from iPhoto.infrastructure.services import thumbnail_artifact
from iPhoto.infrastructure.services.thumbnail_artifact import (
    encode_micro_thumbnail,
    publish_thumbnail_artifact,
    thumbnail_revision,
)
from iPhoto.infrastructure.services.thumbnail_cache_keys import (
    thumbnail_cache_file,
    thumbnail_cache_key,
)
from iPhoto.io.sidecar import save_adjustments
from iPhoto.utils.image_loader import qimage_from_bytes


def test_micro_thumbnail_encoding_uses_pillow_compatible_pixels() -> None:
    image = QImage(32, 16, QImage.Format.Format_ARGB32)
    image.fill(QColor("red"))

    payload = encode_micro_thumbnail(image)

    assert payload is not None
    with Image.open(BytesIO(payload)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == (16, 8)
        red, green, blue = decoded.convert("RGB").getpixel((8, 4))
    assert red > 150 and red > green and red > blue


def test_stable_cache_key_survives_edit_while_revision_changes(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    cache_dir = tmp_path / "thumbs"
    Image.new("RGB", (80, 40), "red").save(source)

    original_revision = thumbnail_revision(source)
    original = publish_thumbnail_artifact(
        source,
        cache_dir,
        expected_revision=original_revision,
    )
    save_adjustments(source, {"Crop_Rotate90": 1.0})
    edited_revision = thumbnail_revision(source)
    edited = publish_thumbnail_artifact(
        source,
        cache_dir,
        expected_revision=edited_revision,
    )

    assert original is not None and edited is not None
    assert original_revision != edited_revision
    assert original.cache_key == edited.cache_key == thumbnail_cache_key(source)
    assert tuple(cache_dir.glob("*.jpg")) == (thumbnail_cache_file(cache_dir, source),)
    micro = qimage_from_bytes(edited.micro_thumbnail)
    assert micro is not None and not micro.isNull()
    assert micro.pixelColor(micro.width() // 2, micro.height() // 2).red() > 150


def test_publication_rejects_render_if_sidecar_changes_midflight(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    cache_dir = tmp_path / "thumbs"
    Image.new("RGB", (32, 32), "white").save(source)
    cache_file = thumbnail_cache_file(cache_dir, source)
    cache_file.parent.mkdir(parents=True)
    Image.new("RGB", (512, 512), "green").save(cache_file)
    expected_revision = thumbnail_revision(source)

    def render(_path: Path, size: QSize, **_kwargs) -> QImage:
        image = QImage(size, QImage.Format.Format_RGB32)
        image.fill(QColor("red"))
        save_adjustments(source, {"Light_Master": 0.5})
        return image

    with patch(
        "iPhoto.infrastructure.services.thumbnail_artifact.render_thumbnail_image",
        side_effect=render,
    ):
        artifact = publish_thumbnail_artifact(
            source,
            cache_dir,
            expected_revision=expected_revision,
        )

    assert artifact is None
    red, green, blue = Image.open(cache_file).getpixel((256, 256))
    assert green > red and green > blue


def test_publication_retries_transient_replace_lock(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    cache_dir = tmp_path / "thumbs"
    Image.new("RGB", (32, 32), "red").save(source)
    revision = thumbnail_revision(source)
    real_replace = thumbnail_artifact.os.replace
    calls = 0

    def flaky_replace(src: Path, dst: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("locked briefly")
        real_replace(src, dst)

    with patch.object(thumbnail_artifact.os, "replace", side_effect=flaky_replace), patch.object(
        thumbnail_artifact.time,
        "sleep",
    ):
        artifact = publish_thumbnail_artifact(
            source,
            cache_dir,
            expected_revision=revision,
        )

    assert artifact is not None
    assert calls == 2
    assert thumbnail_cache_file(cache_dir, source).is_file()


def test_failed_replace_preserves_last_complete_artifact(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    cache_dir = tmp_path / "thumbs"
    Image.new("RGB", (32, 32), "red").save(source)
    cache_file = thumbnail_cache_file(cache_dir, source)
    cache_file.parent.mkdir(parents=True)
    Image.new("RGB", (512, 512), "green").save(cache_file)

    with patch.object(
        thumbnail_artifact.os,
        "replace",
        side_effect=PermissionError("locked"),
    ), patch.object(thumbnail_artifact.time, "sleep"):
        artifact = publish_thumbnail_artifact(
            source,
            cache_dir,
            expected_revision=thumbnail_revision(source),
        )

    assert artifact is None
    red, green, blue = Image.open(cache_file).getpixel((256, 256))
    assert green > red and green > blue
