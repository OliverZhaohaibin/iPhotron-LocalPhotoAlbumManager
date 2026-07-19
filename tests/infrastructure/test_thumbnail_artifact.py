from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage

from iPhoto.infrastructure.services.thumbnail_artifact import (
    ensure_thumbnail_artifact,
)
from iPhoto.infrastructure.services.thumbnail_cache_keys import thumbnail_cache_key
from iPhoto.io.sidecar import save_adjustments
from iPhoto.utils.image_loader import qimage_from_bytes


def test_artifact_key_tracks_sidecar_and_micro_matches_full(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    cache_dir = tmp_path / "thumbs"
    Image.new("RGB", (80, 40), "red").save(source)

    original = ensure_thumbnail_artifact(source, cache_dir)
    assert original is not None

    save_adjustments(source, {"Crop_Rotate90": 1.0})
    edited = ensure_thumbnail_artifact(source, cache_dir)

    assert edited is not None
    assert edited.cache_key != original.cache_key
    assert (cache_dir / f"{original.cache_key}.jpg").is_file()
    assert (cache_dir / f"{edited.cache_key}.jpg").is_file()
    micro = qimage_from_bytes(edited.micro_thumbnail)
    assert micro is not None and not micro.isNull()
    assert micro.pixelColor(micro.width() // 2, micro.height() // 2).red() > 150


def test_artifact_discards_render_when_sidecar_changes_midflight(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    cache_dir = tmp_path / "thumbs"
    Image.new("RGB", (32, 32), "white").save(source)
    calls = 0

    def render(_path: Path, size: QSize, **_kwargs) -> QImage:
        nonlocal calls
        calls += 1
        image = QImage(size, QImage.Format.Format_RGB32)
        if calls == 1:
            image.fill(QColor("red"))
            save_adjustments(source, {"Light_Master": 0.5})
        else:
            image.fill(QColor("blue"))
        return image

    with patch(
        "iPhoto.infrastructure.services.thumbnail_artifact.render_thumbnail_image",
        side_effect=render,
    ):
        artifact = ensure_thumbnail_artifact(source, cache_dir, max_attempts=2)

    assert artifact is not None
    assert calls == 2
    assert artifact.cache_key == thumbnail_cache_key(source)
    assert artifact.image.pixelColor(256, 256).blue() > 200
