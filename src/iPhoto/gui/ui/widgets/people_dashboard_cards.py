"""Card widgets for People clusters and groups."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from iPhoto.people.repository import PeopleGroupSummary, PersonSummary

from .people_dashboard_shared import (
    CARD_HEIGHT,
    CARD_RADIUS,
    CARD_WIDTH,
    GROUP_CARD_HEIGHT,
    GROUP_CARD_RADIUS,
    GROUP_CARD_WIDTH,
    PLACEHOLDER_BACKDROPS,
    _pixmap_from_image_path,
    _qcolor,
    _rounded_path,
)


class PeopleCard(QWidget):
    activated = Signal(str)
    menuRequested = Signal(str, object)

    def __init__(
        self,
        *,
        board: "PeopleBoard",
        summary: PersonSummary,
        seed_index: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.board = board
        self.summary = summary
        self.seed_index = seed_index
        self._hovered = False
        self._dragging = False
        self._press_pos: QPoint | None = None
        self._drag_offset: QPoint | None = None
        self._artwork: QPixmap | None = None
        self._placeholder_artwork: QPixmap | None = None

        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_shadow(blur=28, offset_y=7, alpha=36)

    @property
    def person_id(self) -> str:
        return self.summary.person_id

    def display_name(self) -> str:
        return self.summary.name or "Unnamed"

    def begin_drag(self) -> None:
        self._dragging = True
        self._hovered = False
        self._apply_shadow(blur=42, offset_y=12, alpha=64)
        self.raise_()
        self.board.begin_drag(self)
        self.update()

    def end_drag(self) -> None:
        self._dragging = False
        self._apply_shadow(blur=28, offset_y=7, alpha=36)
        self.update()

    def _apply_shadow(self, *, blur: int, offset_y: int, alpha: int) -> None:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(blur)
        shadow.setOffset(0, offset_y)
        shadow.setColor(QColor(0, 0, 0, alpha))
        self.setGraphicsEffect(shadow)

    def _cover_pixmap(self) -> QPixmap:
        if self._artwork is not None:
            return self._artwork
        if self._placeholder_artwork is None:
            self._placeholder_artwork = self._render_placeholder_art()
        return self._placeholder_artwork

    def load_cover_artwork(self) -> None:
        if self._artwork is not None:
            return
        self._artwork = self._render_cover_art()
        self.update()

    def _render_cover_art(self) -> QPixmap:
        thumbnail_path = self.summary.thumbnail_path
        if thumbnail_path is not None:
            pixmap = _pixmap_from_image_path(thumbnail_path, (CARD_WIDTH * 2, CARD_HEIGHT * 2))
            if pixmap is not None:
                return pixmap
        return self._render_placeholder_art()

    def _render_placeholder_art(self) -> QPixmap:
        scale = 2
        pixmap = QPixmap(CARD_WIDTH * scale, CARD_HEIGHT * scale)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(scale, scale)

        rect = QRectF(0, 0, CARD_WIDTH, CARD_HEIGHT)
        top, bottom = PLACEHOLDER_BACKDROPS[self.seed_index % len(PLACEHOLDER_BACKDROPS)]
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, _qcolor(top))
        gradient.setColorAt(1.0, _qcolor(bottom))
        painter.fillRect(rect, gradient)

        for index, alpha in enumerate((34, 46, 26)):
            radius = 38 + index * 16
            center_x = 26 + index * 40 + (self.seed_index % 3) * 8
            center_y = 30 + index * 22 + (self.seed_index % 2) * 12
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_qcolor("#FFFFFF", alpha))
            painter.drawEllipse(QRectF(center_x, center_y, radius, radius))

        painter.setBrush(_qcolor("#FFFFFF", 54))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(36, 28, 84, 84))
        painter.drawRoundedRect(QRectF(26, 108, 104, 72), 30, 30)

        painter.setBrush(_qcolor("#FFFFFF", 220))
        painter.drawEllipse(QRectF(54, 44, 48, 48))
        painter.drawRoundedRect(QRectF(44, 118, 68, 46), 22, 22)

        initial = self.display_name().strip()[:1].upper() or "?"
        painter.setPen(_qcolor("#0F172A"))
        painter.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))
        painter.drawText(QRectF(0, 42, CARD_WIDTH, 56), Qt.AlignmentFlag.AlignCenter, initial)

        painter.end()
        return pixmap

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        card_rect = QRectF(4, 4, CARD_WIDTH - 8, CARD_HEIGHT - 8)
        card_path = _rounded_path(card_rect, CARD_RADIUS)

        painter.save()
        painter.setClipPath(card_path)
        painter.drawPixmap(card_rect.toRect(), self._cover_pixmap())
        self._paint_bottom_overlay(painter, card_rect)
        self._paint_count_badge(painter, card_rect)
        painter.restore()

        border_color = (
            QColor("#2272F2") if (self._hovered or self._dragging) else QColor(255, 255, 255, 110)
        )
        border_width = 3.0 if (self._hovered or self._dragging) else 1.2
        painter.setPen(QPen(border_color, border_width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(card_rect, CARD_RADIUS, CARD_RADIUS)

    def _paint_bottom_overlay(self, painter: QPainter, card_rect: QRectF) -> None:
        gradient = QLinearGradient(
            card_rect.left(), card_rect.bottom() - 82, card_rect.left(), card_rect.bottom()
        )
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(0.55, QColor(0, 0, 0, 58))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 170))
        painter.fillRect(card_rect, gradient)

        text_rect = card_rect.adjusted(14, 0, -14, -12)
        title_font = QFont("Segoe UI", 14, QFont.Weight.Bold)
        subtitle_font = QFont("Segoe UI", 10, QFont.Weight.Medium)

        shadow_rect = text_rect.translated(0, 1.5)
        painter.setPen(QColor(0, 0, 0, 150))
        painter.setFont(title_font)
        painter.drawText(
            shadow_rect.adjusted(0, 0, 0, -16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            self.display_name(),
        )

        painter.setPen(_qcolor("#FFFFFF"))
        painter.drawText(
            text_rect.adjusted(0, 0, 0, -16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            self.display_name(),
        )

        painter.setFont(subtitle_font)
        painter.setPen(_qcolor("#E5E7EB"))
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            f"{self.summary.face_count} faces",
        )

    def _paint_count_badge(self, painter: QPainter, card_rect: QRectF) -> None:
        badge_rect = QRectF(card_rect.left() + 12, card_rect.top() + 12, 44, 28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_qcolor("#111827", 175))
        painter.drawRoundedRect(badge_rect, 14, 14)
        painter.setPen(_qcolor("#FFFFFF"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(self.summary.face_count))

    def enterEvent(self, _event) -> None:  # noqa: N802
        if not self._dragging:
            self._hovered = True
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hovered = False
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            local_pos = event.position().toPoint()
            self._press_pos = local_pos
            self._drag_offset = local_pos
            self.raise_()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not (event.buttons() & Qt.MouseButton.LeftButton) or self._drag_offset is None:
            super().mouseMoveEvent(event)
            return

        local_pos = event.position().toPoint()
        if not self._dragging and self._press_pos is not None:
            if (local_pos - self._press_pos).manhattanLength() > 4:
                self.begin_drag()

        if self._dragging:
            new_pos = self.mapToParent(local_pos - self._drag_offset)
            self.move(new_pos)
            self.board.check_card_proximity(self)
            self.board.update_card_order(self)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self.board.finish_drag(self)
                self.end_drag()
            else:
                self.activated.emit(self.person_id)
            self._press_pos = None
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.menuRequested.emit(self.person_id, event.globalPos())
        event.accept()


class GroupCard(QWidget):
    activated = Signal(str)

    def __init__(
        self,
        *,
        summary: PeopleGroupSummary,
        seed_index: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.summary = summary
        self.seed_index = seed_index
        self._hovered = False
        self._artwork: QPixmap | None = None
        self._placeholder_artwork: QPixmap | None = None
        self.setFixedSize(GROUP_CARD_WIDTH, GROUP_CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 36))
        self.setGraphicsEffect(shadow)

    @property
    def group_id(self) -> str:
        return self.summary.group_id

    def _cover_pixmap(self) -> QPixmap:
        if self._artwork is not None:
            return self._artwork
        if self._placeholder_artwork is None:
            self._placeholder_artwork = self._render_member_collage(load_member_images=False)
        return self._placeholder_artwork

    def load_cover_artwork(self) -> None:
        if self._artwork is not None:
            return
        self._artwork = self._render_cover_art()
        self.update()

    def _render_cover_art(self) -> QPixmap:
        cover_path = self.summary.cover_asset_path
        if cover_path is not None:
            pixmap = _pixmap_from_image_path(
                cover_path,
                (GROUP_CARD_WIDTH * 2, GROUP_CARD_HEIGHT * 2),
            )
            if pixmap is not None:
                return pixmap
        return self._render_member_collage()

    def _render_member_collage(self, *, load_member_images: bool = True) -> QPixmap:
        scale = 2
        pixmap = QPixmap(GROUP_CARD_WIDTH * scale, GROUP_CARD_HEIGHT * scale)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(scale, scale)

        rect = QRectF(0, 0, GROUP_CARD_WIDTH, GROUP_CARD_HEIGHT)
        top, bottom = PLACEHOLDER_BACKDROPS[self.seed_index % len(PLACEHOLDER_BACKDROPS)]
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, _qcolor(top))
        gradient.setColorAt(1.0, _qcolor(bottom))
        painter.fillRect(rect, gradient)

        members = list(self.summary.members[:4])
        if members:
            columns = 2 if len(members) > 1 else 1
            rows = 2 if len(members) > 2 else 1
            cell_w = GROUP_CARD_WIDTH / columns
            cell_h = GROUP_CARD_HEIGHT / rows
            for index, member in enumerate(members):
                x = (index % columns) * cell_w
                y = (index // columns) * cell_h
                target = QRectF(x, y, cell_w, cell_h)
                member_pixmap = None
                if load_member_images and member.thumbnail_path is not None:
                    member_pixmap = _pixmap_from_image_path(
                        member.thumbnail_path,
                        (int(cell_w * 2), int(cell_h * 2)),
                    )
                if member_pixmap is not None:
                    painter.drawPixmap(target.toRect(), member_pixmap)
                else:
                    painter.fillRect(
                        target,
                        _qcolor(PLACEHOLDER_BACKDROPS[index % len(PLACEHOLDER_BACKDROPS)][0]),
                    )

        painter.end()
        return pixmap

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        card_rect = QRectF(4, 4, GROUP_CARD_WIDTH - 8, GROUP_CARD_HEIGHT - 8)
        card_path = _rounded_path(card_rect, GROUP_CARD_RADIUS)
        painter.save()
        painter.setClipPath(card_path)
        painter.drawPixmap(card_rect.toRect(), self._cover_pixmap())
        self._paint_bottom_overlay(painter, card_rect)
        painter.restore()

        border_color = QColor("#2272F2") if self._hovered else QColor(255, 255, 255, 120)
        border_width = 2.6 if self._hovered else 1.2
        painter.setPen(QPen(border_color, border_width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(card_rect, GROUP_CARD_RADIUS, GROUP_CARD_RADIUS)

    def _paint_bottom_overlay(self, painter: QPainter, card_rect: QRectF) -> None:
        gradient = QLinearGradient(
            card_rect.left(), card_rect.bottom() - 74, card_rect.left(), card_rect.bottom()
        )
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(0.62, QColor(0, 0, 0, 70))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 176))
        painter.fillRect(card_rect, gradient)

        if not self.summary.name:
            return
        text_rect = card_rect.adjusted(14, 0, -14, -13)
        painter.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        painter.setPen(QColor(0, 0, 0, 150))
        painter.drawText(
            text_rect.translated(0, 1.5),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            self.summary.name,
        )
        painter.setPen(_qcolor("#FFFFFF"))
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            self.summary.name,
        )

    def enterEvent(self, _event) -> None:  # noqa: N802
        self._hovered = True
        self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hovered = False
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.group_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)
