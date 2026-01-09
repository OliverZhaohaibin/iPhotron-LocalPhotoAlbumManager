"""Custom delegate for drawing album grid tiles."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem, QStyledItemDelegate

from ..badge_renderer import BadgeRenderer
from ..geometry_utils import calculate_center_crop
from ..models.asset_model import Roles


class AssetGridDelegate(QStyledItemDelegate):
    """Render thumbnails in a tight, borderless grid."""

    _FILMSTRIP_RATIO = 0.6

    def __init__(self, parent=None, *, filmstrip_mode: bool = False) -> None:  # type: ignore[override]
        super().__init__(parent)
        self._filmstrip_mode = filmstrip_mode
        self._base_size = 192
        self._filmstrip_height = 120
        self._filmstrip_border_width = 2
        self._selection_mode_active = False
        self._badge_renderer = BadgeRenderer()

    def set_base_size(self, size: int) -> None:
        """Update the target rendering size for standard grid tiles."""
        self._base_size = size

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # type: ignore[override]
        if bool(index.data(Roles.IS_SPACER)):
            hint = index.data(Qt.ItemDataRole.SizeHintRole)
            if isinstance(hint, QSize) and hint.isValid():
                return QSize(hint.width(), self._filmstrip_height)
            return QSize(0, self._filmstrip_height)

        if not self._filmstrip_mode:
            return QSize(self._base_size, self._base_size)

        is_current = bool(index.data(Roles.IS_CURRENT))
        height = self._filmstrip_height
        if is_current:
            return QSize(height, height)
        width = int(height * self._FILMSTRIP_RATIO)
        return QSize(width, height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        if bool(index.data(Roles.IS_SPACER)):
            return

        painter.save()
        cell_rect = option.rect
        is_current = self._filmstrip_mode and bool(index.data(Roles.IS_CURRENT))
        thumb_rect = cell_rect
        base_color = option.palette.color(QPalette.Base)
        corner_radius = 8.0 if self._filmstrip_mode else 0.0

        pixmap = index.data(Qt.DecorationRole)
        micro_thumb = None
        if not (isinstance(pixmap, QPixmap) and not pixmap.isNull()):
            # Fallback to micro thumbnail
            micro_thumb = index.data(Roles.MICRO_THUMBNAIL)

        clip_path: QPainterPath | None = None
        if self._filmstrip_mode and corner_radius > 0.0:
            clip_path = QPainterPath()
            clip_path.addRoundedRect(QRectF(thumb_rect), corner_radius, corner_radius)
            # Fill the rounded bounds first so uncovered corners inherit the
            # strip background instead of the window behind the transparent view.
            painter.fillPath(clip_path, base_color)
            painter.setClipPath(clip_path)
        elif self._filmstrip_mode:
            painter.fillRect(thumb_rect, base_color)

        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            source_rect = calculate_center_crop(pixmap.size(), thumb_rect.size())
            if not source_rect.isEmpty():
                painter.drawPixmap(QRectF(thumb_rect), pixmap, source_rect)
            else:
                painter.fillRect(thumb_rect, QColor("#1b1b1b"))
        elif isinstance(micro_thumb, QImage) and not micro_thumb.isNull():
            # Draw micro thumbnail scaled
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            # Simple scaling to fill the thumb_rect, using center crop logic
            source_rect = calculate_center_crop(micro_thumb.size(), thumb_rect.size())
            if not source_rect.isEmpty():
                # We can draw QImage directly. QPainter handles scaling.
                # Since it's a tiny image, SmoothPixmapTransform (bilinear) is important.
                painter.drawImage(QRectF(thumb_rect), micro_thumb, QRectF(source_rect))
            else:
                painter.fillRect(thumb_rect, QColor("#1b1b1b"))
        else:
            painter.fillRect(thumb_rect, QColor("#1b1b1b"))

        if option.state & QStyle.State_Selected:
            painter.save()
            if clip_path is not None:
                painter.setClipPath(clip_path)
            highlight = option.palette.color(QPalette.Highlight)
            overlay = QColor(highlight)
            overlay.setAlpha(60 if is_current and self._filmstrip_mode else 110)
            painter.fillRect(thumb_rect, overlay)
            painter.restore()

        if clip_path is not None:
            painter.setClipPath(QPainterPath())

        if self._filmstrip_mode and is_current:
            highlight = option.palette.color(QPalette.Highlight)
            pen = QPen(highlight, self._filmstrip_border_width)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            adjusted = thumb_rect.adjusted(1, 1, -1, -1)
            radius = max(0.0, corner_radius - 1)
            painter.drawRoundedRect(QRectF(adjusted), radius, radius)

        if index.data(Roles.IS_LIVE):
            self._badge_renderer.draw_live_badge(painter, thumb_rect)

        if index.data(Roles.IS_PANO):
            self._badge_renderer.draw_pano_badge(painter, thumb_rect)

        if index.data(Roles.IS_VIDEO):
            duration = self._extract_duration(index)
            if duration > 0:
                self._badge_renderer.draw_duration_badge(painter, thumb_rect, duration, option.font)

        if bool(index.data(Roles.FEATURED)):
            self._badge_renderer.draw_favorite_badge(painter, thumb_rect)

        if (
            self._selection_mode_active
            and not self._filmstrip_mode
            and option.state & QStyle.State_Selected
        ):
            self._badge_renderer.draw_selection_badge(painter, thumb_rect)

        painter.restore()

    def set_selection_mode_active(self, enabled: bool) -> None:
        """Toggle the presence of the selection confirmation badge."""

        self._selection_mode_active = bool(enabled)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_duration(index) -> float:
        """Safely extract the duration from the size role data."""
        size_info = index.data(Roles.SIZE)
        if isinstance(size_info, dict):
            raw = size_info.get("duration")  # type: ignore[arg-type]
            if isinstance(raw, (int, float)):
                return max(0.0, float(raw))
        return 0.0
