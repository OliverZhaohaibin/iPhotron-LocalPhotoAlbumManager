"""Edit-aware rendering with stable cache paths and revision-guarded publication."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image as PILImage
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPainter

from iPhoto.application.ports import EditServicePort
from iPhoto.core.color_resolver import compute_color_statistics
from iPhoto.core.geometry import apply_geometry_and_crop
from iPhoto.core.image_filters import apply_adjustments
from iPhoto.infrastructure.services.thumbnail_cache_keys import (
    DEFAULT_THUMBNAIL_SIZE,
    thumbnail_cache_file,
)
from iPhoto.infrastructure.services.thumbnail_generator import PillowThumbnailGenerator
from iPhoto.io import sidecar
from iPhoto.utils import image_loader

THUMBNAIL_RENDER_VERSION = "gallery-stable-path-edit-aware-v1"
_LOCK_STRIPES = tuple(threading.RLock() for _ in range(257))
_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_SECONDS = 0.025


@dataclass(frozen=True, slots=True)
class ThumbnailArtifact:
    cache_key: str
    revision: str
    image: QImage
    micro_thumbnail: bytes


def thumbnail_revision(path: Path) -> str:
    """Compute a durable render-input revision outside cache-hit hot paths."""

    source = Path(path)
    try:
        stat_result = source.stat()
        source_mtime_ns = int(
            getattr(
                stat_result,
                "st_mtime_ns",
                int(stat_result.st_mtime * 1_000_000_000),
            )
        )
        source_size = int(stat_result.st_size)
    except OSError:
        source_mtime_ns = 0
        source_size = 0
    try:
        sidecar_payload = sidecar.sidecar_path_for_asset(source).read_bytes()
    except OSError:
        sidecar_payload = b""
    sidecar_digest = hashlib.blake2b(sidecar_payload, digest_size=16).hexdigest()
    normalized = Path(os.path.abspath(os.fspath(source.expanduser()))).as_posix()
    payload = "\0".join(
        (
            THUMBNAIL_RENDER_VERSION,
            normalized,
            str(source_mtime_ns),
            str(source_size),
            sidecar_digest,
        )
    )
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=20).hexdigest()


def _lock_index(path: Path) -> int:
    normalized = Path(os.path.abspath(os.fspath(Path(path).expanduser()))).as_posix()
    digest = hashlib.blake2b(normalized.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") % len(_LOCK_STRIPES)


@contextmanager
def thumbnail_artifact_lock(path: Path) -> Iterator[None]:
    """Serialize sidecar commits and stable-path artifact replacement per asset."""

    lock = _LOCK_STRIPES[_lock_index(path)]
    with lock:
        yield


def render_thumbnail_image(
    path: Path,
    size: QSize,
    *,
    edit_service: EditServicePort | None = None,
    generator: PillowThumbnailGenerator | None = None,
) -> QImage | None:
    """Render one square thumbnail with persisted sidecar adjustments applied."""

    if size.isEmpty() or not size.isValid():
        return None
    source = Path(path)
    resolved_generator = generator or PillowThumbnailGenerator()
    is_video = source.suffix.lower() in {
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".m4v",
    }
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
        adjustments = sidecar.resolve_render_adjustments(raw_adjustments, color_stats=stats)
    if adjustments:
        image = apply_geometry_and_crop(image, adjustments) or image
        image = apply_adjustments(image, adjustments, color_stats=stats)
    return _composite_square(image, size)


def encode_micro_thumbnail(image: QImage) -> bytes | None:
    micro = image.scaled(
        QSize(16, 16),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return _encode_qimage_jpeg(micro, quality=75)


def publish_thumbnail_artifact(
    path: Path,
    cache_dir: Path,
    *,
    expected_revision: str,
    size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
    edit_service: EditServicePort | None = None,
    generator: PillowThumbnailGenerator | None = None,
) -> ThumbnailArtifact | None:
    """Render and atomically replace the stable cache file iff inputs stay current."""

    source = Path(path)
    if thumbnail_revision(source) != expected_revision:
        return None
    target_size = QSize(int(size[0]), int(size[1]))
    image = render_thumbnail_image(
        source,
        target_size,
        edit_service=edit_service,
        generator=generator,
    )
    if image is None or image.isNull():
        return None
    micro = encode_micro_thumbnail(image)
    if micro is None:
        return None
    encoded_image = _encode_qimage_jpeg(image)
    if encoded_image is None:
        return None
    cache_file = thumbnail_cache_file(cache_dir, source, size)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_file.with_name(
        f".{cache_file.name}.{threading.get_ident()}.{time.monotonic_ns()}.tmp"
    )
    try:
        temporary.write_bytes(encoded_image)
        with thumbnail_artifact_lock(source):
            if thumbnail_revision(source) != expected_revision:
                return None
            if not _replace_cache_file(temporary, cache_file):
                return None
    except OSError:
        return None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return ThumbnailArtifact(
        cache_key=cache_file.stem,
        revision=expected_revision,
        image=image,
        micro_thumbnail=micro,
    )


def _encode_qimage_jpeg(image: QImage, *, quality: int = 75) -> bytes | None:
    """Encode a QImage without entering Qt's process-global image plugins."""

    if image.isNull():
        return None
    try:
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width = rgba.width()
        height = rgba.height()
        if width <= 0 or height <= 0:
            return None
        pixels = bytes(rgba.bits())
        pil_image = PILImage.frombytes(
            "RGBA",
            (width, height),
            pixels,
            "raw",
            "RGBA",
            rgba.bytesPerLine(),
            1,
        ).convert("RGB")
        payload = BytesIO()
        pil_image.save(payload, format="JPEG", quality=quality)
        return payload.getvalue()
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _replace_cache_file(temporary: Path, cache_file: Path) -> bool:
    """Atomically publish despite short-lived Windows decoder/AV file locks."""

    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(temporary, cache_file)
            return True
        except PermissionError:
            if attempt + 1 == _REPLACE_ATTEMPTS:
                return False
            time.sleep(_REPLACE_BACKOFF_SECONDS * (attempt + 1))
    return False


def _composite_square(image: QImage, size: QSize) -> QImage:
    canvas = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    scaled = image.scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    source_rect = scaled.rect()
    target_rect = canvas.rect()
    if source_rect.width() > target_rect.width():
        difference = source_rect.width() - target_rect.width()
        source_rect.adjust(difference // 2, 0, -(difference - difference // 2), 0)
    if source_rect.height() > target_rect.height():
        difference = source_rect.height() - target_rect.height()
        source_rect.adjust(0, difference // 2, 0, -(difference - difference // 2))
    painter = QPainter(canvas)
    painter.drawImage(target_rect, scaled, source_rect)
    painter.end()
    return canvas


__all__ = [
    "ThumbnailArtifact",
    "encode_micro_thumbnail",
    "publish_thumbnail_artifact",
    "render_thumbnail_image",
    "thumbnail_artifact_lock",
    "thumbnail_revision",
]
