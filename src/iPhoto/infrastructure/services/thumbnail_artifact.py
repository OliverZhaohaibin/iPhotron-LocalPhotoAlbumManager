"""Shared edit-aware thumbnail rendering and immutable artifact publication."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter

from iPhoto.application.ports import EditServicePort
from iPhoto.core.color_resolver import compute_color_statistics
from iPhoto.core.geometry import apply_geometry_and_crop
from iPhoto.core.image_filters import apply_adjustments
from iPhoto.infrastructure.services.thumbnail_cache_keys import (
    DEFAULT_THUMBNAIL_SIZE,
    thumbnail_cache_file_for_key,
    thumbnail_fingerprint,
)
from iPhoto.infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from iPhoto.io import sidecar
from iPhoto.utils import image_loader


@dataclass(frozen=True, slots=True)
class ThumbnailArtifact:
    cache_key: str
    image: QImage
    micro_thumbnail: bytes


def render_thumbnail_image(
    path: Path,
    size: QSize,
    *,
    edit_service: EditServicePort | None = None,
    generator: PillowThumbnailGenerator | None = None,
) -> QImage | None:
    """Render one full thumbnail from a single persisted edit snapshot."""

    if size.isEmpty() or not size.isValid():
        return None
    source = Path(path)
    resolved_generator = generator or PillowThumbnailGenerator()
    is_video = source.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
    image = None if is_video else image_loader.load_qimage(source, size)
    if image is None or image.isNull():
        pil_image = resolved_generator.generate(source, (size.width(), size.height()))
        if pil_image is None:
            return None
        image = image_loader.qimage_from_pil(pil_image)
    if image is None or image.isNull():
        return None

    if edit_service is not None and edit_service.sidecar_exists(source):
        stats = compute_color_statistics(image)
        state = edit_service.describe_adjustments(source, color_stats=stats)
        adjustments = state.resolved_adjustments
    else:
        raw_adjustments = sidecar.load_adjustments(source)
        stats = compute_color_statistics(image) if raw_adjustments else None
        adjustments = sidecar.resolve_render_adjustments(
            raw_adjustments,
            color_stats=stats,
        )
    if adjustments:
        image = apply_geometry_and_crop(image, adjustments) or image
        image = apply_adjustments(image, adjustments, color_stats=stats)
    return composite_square_thumbnail(image, size)


def composite_square_thumbnail(image: QImage, size: QSize) -> QImage:
    canvas = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    scaled = image.scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    target_rect = canvas.rect()
    source_rect = scaled.rect()
    if source_rect.width() > target_rect.width():
        diff = source_rect.width() - target_rect.width()
        source_rect.adjust(diff // 2, 0, -(diff - diff // 2), 0)
    if source_rect.height() > target_rect.height():
        diff = source_rect.height() - target_rect.height()
        source_rect.adjust(0, diff // 2, 0, -(diff - diff // 2))
    painter.drawImage(target_rect, scaled, source_rect)
    painter.end()
    return canvas


def encode_micro_thumbnail(image: QImage) -> bytes | None:
    micro = image.scaled(
        QSize(16, 16),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    ).convertToFormat(QImage.Format.Format_RGB888)
    payload = QByteArray()
    buffer = QBuffer(payload)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        return None
    try:
        if not micro.save(buffer, "JPEG", 75):
            return None
        return bytes(payload)
    finally:
        buffer.close()


def ensure_thumbnail_artifact(
    path: Path,
    cache_dir: Path,
    *,
    size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
    edit_service: EditServicePort | None = None,
    generator: PillowThumbnailGenerator | None = None,
    max_attempts: int = 2,
) -> ThumbnailArtifact | None:
    """Load or atomically publish the artifact for the current source/edit revision."""

    source = Path(path)
    target_size = QSize(int(size[0]), int(size[1]))
    for _attempt in range(max(1, int(max_attempts))):
        before = thumbnail_fingerprint(source, size)
        cache_file = thumbnail_cache_file_for_key(cache_dir, before.cache_key)
        image = QImage()
        if cache_file.is_file():
            reader = QImageReader(str(cache_file))
            reader.setAutoTransform(True)
            image = reader.read()
        if image.isNull():
            rendered = render_thumbnail_image(
                source,
                target_size,
                edit_service=edit_service,
                generator=generator,
            )
            if rendered is None or rendered.isNull():
                return None
            after_render = thumbnail_fingerprint(source, size)
            if after_render != before:
                continue
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_file.with_name(
                f".{cache_file.name}.{threading.get_ident()}.{time.monotonic_ns()}.tmp"
            )
            try:
                if not rendered.save(str(temporary), "JPEG"):
                    return None
                if thumbnail_fingerprint(source, size) != before:
                    continue
                temporary.replace(cache_file)
                image = rendered
            except OSError:
                return None
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        if thumbnail_fingerprint(source, size) != before:
            continue
        micro = encode_micro_thumbnail(image)
        if micro is None:
            return None
        return ThumbnailArtifact(before.cache_key, image, micro)
    return None


__all__ = [
    "ThumbnailArtifact",
    "composite_square_thumbnail",
    "encode_micro_thumbnail",
    "ensure_thumbnail_artifact",
    "render_thumbnail_image",
]
