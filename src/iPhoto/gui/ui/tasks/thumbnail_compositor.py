"""Thumbnail canvas composition logic (square thumbnail with background)."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPainter


def composite_canvas(image: QImage, size: QSize) -> QImage:  # pragma: no cover - worker helper
    """Scale *image* to fill *size* and centre-crop onto a transparent canvas."""
    canvas = QImage(size, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    scaled = image.scaled(
        size,
        Qt.KeepAspectRatioByExpanding,
        Qt.SmoothTransformation,
    )
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    target_rect = canvas.rect()
    source_rect = scaled.rect()
    if source_rect.width() > target_rect.width():
        diff = source_rect.width() - target_rect.width()
        left = diff // 2
        right = diff - left
        source_rect.adjust(left, 0, -right, 0)
    if source_rect.height() > target_rect.height():
        diff = source_rect.height() - target_rect.height()
        top = diff // 2
        bottom = diff - top
        source_rect.adjust(0, top, 0, -bottom)
    painter.drawImage(target_rect, scaled, source_rect)
    painter.end()
    return canvas
