"""Helper for rendering asset badges (duration, live, etc.)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QStaticText,
    QTransform,
)

from .icons import load_icon


class BadgeRenderer:
    """Renderer for overlay badges on asset thumbnails."""

    def __init__(self) -> None:
        self._live_icon: QIcon = load_icon("livephoto.svg", color="white")
        self._favorite_icon: QIcon = load_icon("suit.heart.fill.svg", color="#ff4d67")
        self._pano_icon: QIcon = load_icon("pano.svg", color="white")
        self._selection_icon: QIcon = load_icon("checkmark.circle.svg")

        self._duration_font: Optional[QFont] = None
        self._duration_text_cache: dict[str, QStaticText] = {}

    def draw_duration_badge(
        self,
        painter: QPainter,
        rect: QRect,
        duration: float,
        font: QFont,
    ) -> None:
        """Draw the video duration badge."""
        if duration <= 0:
            return

        text = self._format_duration(duration)
        if not text:
            return

        # Prepare font
        if self._duration_font is None:
            # Clone the passed font and adjust
            badge_font = QFont(font)
            badge_font.setPointSizeF(max(9.0, font.pointSizeF() - 1))
            badge_font.setBold(True)
            self._duration_font = badge_font

        target_font = self._duration_font

        # Cache QStaticText
        if text not in self._duration_text_cache:
            static_text = QStaticText(text)
            # Use default transform to be view-agnostic
            static_text.prepare(QTransform(), target_font)
            self._duration_text_cache[text] = static_text

        static_text = self._duration_text_cache[text]

        padding = 6
        text_size = static_text.size()
        width = int(text_size.width()) + padding * 2
        height = int(text_size.height()) + padding

        badge_rect = QRect(
            rect.right() - width - 8,
            rect.bottom() - height - 8,
            width,
            height,
        )

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawRoundedRect(badge_rect, 6, 6)
        painter.setPen(QColor("white"))
        painter.setFont(target_font)

        text_x = badge_rect.x() + (badge_rect.width() - text_size.width()) / 2
        text_y = badge_rect.y() + (badge_rect.height() - text_size.height()) / 2
        painter.drawStaticText(int(text_x), int(text_y), static_text)
        painter.restore()

    def draw_live_badge(self, painter: QPainter, rect: QRect) -> None:
        """Draw the Live Photo badge."""
        if self._live_icon.isNull():
            return

        padding = 6
        icon_size = 18
        badge_width = icon_size + padding * 2
        badge_height = icon_size + padding * 2
        badge_rect = QRect(rect.left() + 8, rect.top() + 8, badge_width, badge_height)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 140))
        painter.drawRoundedRect(badge_rect, 6, 6)
        icon_rect = QRect(
            badge_rect.left() + padding,
            badge_rect.top() + padding,
            icon_size,
            icon_size,
        )
        self._live_icon.paint(painter, icon_rect)
        painter.restore()

    def draw_pano_badge(self, painter: QPainter, rect: QRect) -> None:
        """Draw the panorama badge."""
        if self._pano_icon.isNull():
            return

        padding = 6
        icon_size = 18
        badge_width = icon_size + padding * 2
        badge_height = icon_size + padding * 2
        badge_rect = QRect(
            rect.right() - badge_width - 8,
            rect.bottom() - badge_height - 8,
            badge_width,
            badge_height,
        )

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawRoundedRect(badge_rect, 6, 6)
        icon_rect = QRect(
            badge_rect.left() + padding,
            badge_rect.top() + padding,
            icon_size,
            icon_size,
        )
        self._pano_icon.paint(painter, icon_rect)
        painter.restore()

    def draw_favorite_badge(self, painter: QPainter, rect: QRect) -> None:
        """Draw the favorite heart badge."""
        if self._favorite_icon.isNull():
            return

        padding = 5
        icon_size = 16
        badge_width = icon_size + padding * 2
        badge_height = icon_size + padding * 2
        badge_rect = QRect(
            rect.left() + 8,
            rect.bottom() - badge_height - 8,
            badge_width,
            badge_height,
        )

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawRoundedRect(badge_rect, 6, 6)
        icon_rect = badge_rect.adjusted(padding, padding, -padding, -padding)
        self._favorite_icon.paint(painter, icon_rect)
        painter.restore()

    def draw_selection_badge(self, painter: QPainter, rect: QRect) -> None:
        """Draw the selection checkmark."""
        if self._selection_icon.isNull():
            return

        margin = 10
        badge_size = 30
        badge_rect = QRect(
            rect.right() - badge_size - margin,
            rect.bottom() - badge_size - margin,
            badge_size,
            badge_size,
        )
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        pixmap = self._selection_icon.pixmap(badge_rect.size())
        painter.drawPixmap(badge_rect, pixmap)
        painter.restore()

    @staticmethod
    def _format_duration(duration: float) -> str:
        seconds = int(round(duration))
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:d}:{secs:02d}"
