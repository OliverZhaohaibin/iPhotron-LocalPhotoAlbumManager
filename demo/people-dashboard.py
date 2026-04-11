from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import Iterable

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPoint, QParallelAnimationGroup, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


CARD_WIDTH = 156
CARD_HEIGHT = 212
CARD_RADIUS = 24
SPACING = 18
SUB_SPACING = 26
PROXIMITY_THRESHOLD = 120
CANVAS_MARGIN = 18

MENU_STYLE = """
QMenu {
    background-color: #FFFFFF;
    border: 1px solid rgba(17, 24, 39, 0.12);
    border-radius: 14px;
    padding: 8px;
}
QMenu::item {
    background-color: transparent;
    color: #111827;
    padding: 8px 18px;
    border-radius: 10px;
}
QMenu::item:selected {
    background-color: #EAF2FF;
    color: #1D4ED8;
}
QMenu::separator {
    height: 1px;
    background: rgba(17, 24, 39, 0.10);
    margin: 6px 10px;
}
"""


def _qcolor(value: str | QColor, alpha: int | None = None) -> QColor:
    color = QColor(value) if not isinstance(value, QColor) else QColor(value)
    if alpha is not None:
        color.setAlpha(alpha)
    return color


def _rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _create_pos_anim(widget: QWidget, target: QPoint, duration: int = 240) -> QPropertyAnimation:
    anim = QPropertyAnimation(widget, b"pos")
    anim.setDuration(duration)
    anim.setStartValue(widget.pos())
    anim.setEndValue(target)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    return anim


def _button_distance(btn1: QWidget, btn2: QWidget) -> float:
    c1 = btn1.pos() + QPoint(CARD_WIDTH // 2, CARD_HEIGHT // 2)
    c2 = btn2.pos() + QPoint(CARD_WIDTH // 2, CARD_HEIGHT // 2)
    return math.hypot(c1.x() - c2.x(), c1.y() - c2.y())


@dataclass(frozen=True)
class PortraitPalette:
    kind: str
    top: str
    bottom: str
    skin: str
    hair: str
    outfit: str
    accent: str
    favorite: bool = False


PALETTES: list[PortraitPalette] = [
    PortraitPalette("person", "#5A7C6A", "#20352C", "#EFC4A3", "#45403D", "#1B2621", "#D8F3E3"),
    PortraitPalette("person", "#A54C53", "#3C2024", "#F1D7BF", "#CFC9B8", "#8E2537", "#F9D6D8"),
    PortraitPalette("person", "#C69B6E", "#6A4427", "#E2B18A", "#67412E", "#3A251A", "#F3DFC7"),
    PortraitPalette("person", "#A2B3AF", "#4D5E5B", "#E6C0A5", "#5F5B4A", "#314341", "#DAE7E3"),
    PortraitPalette("person", "#668B6E", "#25352B", "#E7C1A6", "#4C4139", "#22362B", "#D9E7D8"),
    PortraitPalette("person", "#D6D6D6", "#8D939D", "#E2B196", "#3C302B", "#5E87A7", "#F2F5FB", True),
    PortraitPalette("pet", "#D2B08C", "#7A4D2E", "#C88954", "#5C3822", "#EAD3BE", "#FFF0E0"),
    PortraitPalette("person", "#8C9899", "#2D383B", "#E6C3AB", "#7D6856", "#1C2226", "#D7DDDF"),
    PortraitPalette("person", "#5D677A", "#232A35", "#F2D7C3", "#312C2A", "#A3B4D4", "#E7ECF8"),
    PortraitPalette("person", "#C3B29D", "#6B5A4F", "#DFA785", "#705141", "#98C150", "#EEF4DA"),
    PortraitPalette("person", "#35383F", "#121417", "#E3BF9D", "#18191B", "#16181C", "#D0D4DA"),
    PortraitPalette("person", "#A57040", "#3A291C", "#E0B190", "#5A3D2D", "#24252A", "#F2DFC7"),
    PortraitPalette("pet", "#B7B7B0", "#4E4A46", "#D6C19A", "#70624D", "#F4ECDD", "#FAF4E7"),
    PortraitPalette("person", "#A9B8C9", "#415166", "#E7CDB8", "#F7F6F2", "#2B3B50", "#ECF2F8"),
]


SAMPLE_PEOPLE: list[tuple[str | None, PortraitPalette]] = [
    ("Dylan", PALETTES[0]),
    ("Elaine", PALETTES[1]),
    (None, PALETTES[2]),
    ("Ryan", PALETTES[3]),
    ("Noah", PALETTES[4]),
    ("Dylan", PALETTES[5]),
    (None, PALETTES[6]),
    ("Ava", PALETTES[7]),
    ("Lina", PALETTES[8]),
    (None, PALETTES[9]),
    ("Milo", PALETTES[10]),
    (None, PALETTES[11]),
    ("Pip", PALETTES[12]),
    (None, PALETTES[13]),
]


class HintFrame(QFrame):
    def __init__(self, parent: QWidget, style_sheet: str) -> None:
        super().__init__(parent)
        self.setStyleSheet(style_sheet)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()


class GroupBackground(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            """
            QFrame {
                background: rgba(255, 255, 255, 0.70);
                border: 2px dashed rgba(126, 135, 148, 0.9);
                border-radius: 26px;
            }
            """
        )
        self.lower()
        self.hide()


class PeopleCard(QWidget):
    def __init__(
        self,
        *,
        board: "PeopleBoard",
        palette: PortraitPalette | None = None,
        name: str | None = None,
        group: bool = False,
        seed_index: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.board = board
        self.palette = palette
        self.person_name = name
        self.seed_index = seed_index

        self.is_group = group
        self.is_expanded = False
        self.is_sub_card = False
        self.parent_group: PeopleCard | None = None
        self.members: list[PeopleCard] = []

        self._hovered = False
        self._dragging = False
        self._press_pos: QPoint | None = None
        self._drag_offset: QPoint | None = None
        self._artwork: QPixmap | None = None
        self._background: GroupBackground | None = None

        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_shadow(blur=28, offset_y=7, alpha=36)

    def display_name(self) -> str | None:
        if self.is_group:
            return self.group_title()
        return self.person_name

    def sort_key(self) -> tuple[int, str, int]:
        name = (self.display_name() or "").strip().lower()
        has_name = 0 if name else 1
        return has_name, name, self.seed_index

    def group_title(self) -> str:
        named_members = [member.person_name for member in self.members if member.person_name]
        if named_members:
            first = named_members[0]
            remainder = len(self.members) - 1
            return f"{first} +{remainder}" if remainder > 0 else first
        return f"{len(self.members)} People"

    def ensure_background(self) -> GroupBackground:
        if self._background is None:
            self._background = GroupBackground(self.board)
        return self._background

    def set_name(self, new_name: str | None) -> None:
        normalized = (new_name or "").strip()
        self.person_name = normalized or None
        self.update()
        if self.parent_group is not None:
            self.parent_group.update()

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

    def _portrait_pixmap(self) -> QPixmap:
        if self._artwork is None:
            self._artwork = self._render_portrait_art()
        return self._artwork

    def _render_portrait_art(self) -> QPixmap:
        scale = 2
        pixmap = QPixmap(CARD_WIDTH * scale, CARD_HEIGHT * scale)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(scale, scale)

        rect = QRectF(0, 0, CARD_WIDTH, CARD_HEIGHT)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, _qcolor(self.palette.top))
        gradient.setColorAt(1.0, _qcolor(self.palette.bottom))
        painter.fillRect(rect, gradient)

        for index, alpha in enumerate((34, 46, 26)):
            radius = 38 + index * 16
            center_x = 28 + index * 42 + (self.seed_index % 3) * 8
            center_y = 38 + index * 24 + (self.seed_index % 2) * 16
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_qcolor("#FFFFFF", alpha))
            painter.drawEllipse(QRectF(center_x, center_y, radius, radius))

        if self.palette.kind == "pet":
            self._paint_pet(painter, rect)
        else:
            self._paint_person(painter, rect)

        painter.end()
        return pixmap

    def _paint_person(self, painter: QPainter, rect: QRectF) -> None:
        face = QRectF(rect.width() * 0.27, rect.height() * 0.18, rect.width() * 0.46, rect.height() * 0.42)
        neck = QRectF(rect.width() * 0.43, rect.height() * 0.51, rect.width() * 0.14, rect.height() * 0.09)
        shoulders = QRectF(rect.width() * 0.09, rect.height() * 0.61, rect.width() * 0.82, rect.height() * 0.30)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_qcolor(self.palette.outfit))
        painter.drawRoundedRect(shoulders, 32, 32)

        painter.setBrush(_qcolor(self.palette.skin))
        painter.drawRoundedRect(neck, 10, 10)
        painter.drawEllipse(face)

        hair_color = _qcolor(self.palette.hair)
        painter.setBrush(hair_color)
        hair_cap = QRectF(face.left() - 4, face.top() - 8, face.width() + 8, face.height() * 0.60)
        painter.drawEllipse(hair_cap)

        side_hair = QPainterPath()
        side_hair.moveTo(face.left() + 6, face.top() + 30)
        side_hair.quadTo(face.left() - 8, face.center().y(), face.left() + 12, face.bottom() - 16)
        side_hair.lineTo(face.left() + 34, face.bottom() - 12)
        side_hair.quadTo(face.left() + 8, face.center().y(), face.left() + 18, face.top() + 6)
        painter.drawPath(side_hair)

        eye_pen = QPen(_qcolor("#2B1D1A", 200))
        eye_pen.setWidthF(2.2)
        painter.setPen(eye_pen)
        left_eye_y = face.top() + face.height() * 0.54
        painter.drawLine(face.left() + face.width() * 0.28, left_eye_y, face.left() + face.width() * 0.40, left_eye_y + 2)
        painter.drawLine(face.left() + face.width() * 0.60, left_eye_y + 2, face.left() + face.width() * 0.72, left_eye_y)

        painter.drawArc(
            QRectF(face.left() + face.width() * 0.30, face.top() + face.height() * 0.56, face.width() * 0.40, face.height() * 0.24),
            200 * 16,
            140 * 16,
        )

        painter.setBrush(_qcolor("#FFFFFF", 56))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(face.left() + 18, face.top() + 16, face.width() * 0.16, face.height() * 0.18))

    def _paint_pet(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        body = QRectF(rect.width() * 0.17, rect.height() * 0.56, rect.width() * 0.66, rect.height() * 0.30)
        head = QRectF(rect.width() * 0.24, rect.height() * 0.19, rect.width() * 0.52, rect.height() * 0.44)

        painter.setBrush(_qcolor(self.palette.outfit))
        painter.drawEllipse(body)

        painter.setBrush(_qcolor(self.palette.skin))
        painter.drawEllipse(head)

        left_ear = QPolygonF(
            [
                head.topLeft() + QPoint(24, 34),
                head.topLeft() + QPoint(56, -8),
                head.topLeft() + QPoint(82, 50),
            ]
        )
        right_ear = QPolygonF(
            [
                head.topRight() + QPoint(-24, 34),
                head.topRight() + QPoint(-56, -8),
                head.topRight() + QPoint(-82, 50),
            ]
        )
        painter.setBrush(_qcolor(self.palette.hair))
        painter.drawPolygon(left_ear)
        painter.drawPolygon(right_ear)

        stripe_pen = QPen(_qcolor(self.palette.hair, 170))
        stripe_pen.setWidthF(3.0)
        painter.setPen(stripe_pen)
        painter.drawArc(QRectF(head.left() + 12, head.top() + 10, 36, 24), 10 * 16, 120 * 16)
        painter.drawArc(QRectF(head.right() - 48, head.top() + 12, 36, 24), 45 * 16, 120 * 16)
        painter.drawArc(QRectF(head.center().x() - 14, head.top() + 20, 28, 20), 20 * 16, 140 * 16)

        painter.setPen(QPen(_qcolor("#33251D"), 2.2))
        eye_y = head.top() + head.height() * 0.52
        painter.drawEllipse(QRectF(head.left() + 40, eye_y, 10, 10))
        painter.drawEllipse(QRectF(head.right() - 50, eye_y, 10, 10))

        painter.setBrush(_qcolor("#9A6054"))
        painter.setPen(Qt.PenStyle.NoPen)
        nose = QPolygonF(
            [
                QPoint(int(head.center().x()), int(head.top() + head.height() * 0.62)),
                QPoint(int(head.center().x() - 8), int(head.top() + head.height() * 0.68)),
                QPoint(int(head.center().x() + 8), int(head.top() + head.height() * 0.68)),
            ]
        )
        painter.drawPolygon(nose)

        whisker_pen = QPen(_qcolor("#EADFCC", 170))
        whisker_pen.setWidthF(1.5)
        painter.setPen(whisker_pen)
        base_y = head.top() + head.height() * 0.70
        for delta in (-6, 2, 10):
            painter.drawLine(head.center().x() - 12, base_y + delta, head.left() + 10, base_y + delta - 3)
            painter.drawLine(head.center().x() + 12, base_y + delta, head.right() - 10, base_y + delta - 3)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        card_rect = QRectF(4, 4, CARD_WIDTH - 8, CARD_HEIGHT - 8)
        card_path = _rounded_path(card_rect, CARD_RADIUS)

        painter.save()
        painter.setClipPath(card_path)
        if self.is_group:
            self._paint_group_card(painter, card_rect)
        else:
            painter.drawPixmap(card_rect.toRect(), self._portrait_pixmap())
            self._paint_bottom_overlay(painter, card_rect, self.person_name)
            if self.palette and self.palette.favorite:
                self._paint_heart_badge(painter, card_rect)
        painter.restore()

        border_color = QColor("#2272F2") if (self._hovered or self._dragging) else QColor(255, 255, 255, 110)
        border_width = 3.0 if (self._hovered or self._dragging) else 1.2
        painter.setPen(QPen(border_color, border_width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(card_rect, CARD_RADIUS, CARD_RADIUS)

    def _paint_group_card(self, painter: QPainter, card_rect: QRectF) -> None:
        preview_members = list(self.members[:3]) or [self]
        offsets = [QPoint(-10, 10), QPoint(-4, 5), QPoint(0, 0)]
        for index, member in enumerate(preview_members[: len(offsets)]):
            inner = card_rect.adjusted(offsets[index].x(), -offsets[index].y(), offsets[index].x(), -offsets[index].y())
            inner = inner.adjusted(10, 12, -10, -12)
            path = _rounded_path(inner, CARD_RADIUS - 6)
            painter.save()
            painter.setClipPath(path)
            painter.setOpacity(0.54 + index * 0.18)
            painter.drawPixmap(inner.toRect(), member._portrait_pixmap())
            painter.restore()

        self._paint_bottom_overlay(painter, card_rect, self.group_title())

        badge_rect = QRectF(card_rect.left() + 12, card_rect.top() + 12, 38, 28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_qcolor("#111827", 175))
        painter.drawRoundedRect(badge_rect, 14, 14)
        painter.setPen(_qcolor("#FFFFFF"))
        badge_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(badge_font)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(len(self.members)))

    def _paint_bottom_overlay(self, painter: QPainter, card_rect: QRectF, title: str | None) -> None:
        gradient = QLinearGradient(card_rect.left(), card_rect.bottom() - 72, card_rect.left(), card_rect.bottom())
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(0.55, QColor(0, 0, 0, 55))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 160))
        painter.fillRect(card_rect, gradient)

        if title:
            text_rect = card_rect.adjusted(14, 0, -14, -14)
            font = QFont("Segoe UI", 14, QFont.Weight.Bold)
            painter.setFont(font)

            shadow_rect = text_rect.translated(0, 1.5)
            painter.setPen(QColor(0, 0, 0, 150))
            painter.drawText(shadow_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, title)

            painter.setPen(_qcolor("#FFFFFF"))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, title)

    def _paint_heart_badge(self, painter: QPainter, card_rect: QRectF) -> None:
        heart_box = QRectF(card_rect.right() - 34, card_rect.top() + 10, 20, 18)
        cx = heart_box.center().x()
        cy = heart_box.center().y()
        heart = QPainterPath()
        heart.moveTo(cx, cy + 7)
        heart.cubicTo(cx - 10, cy - 1, cx - 9, cy - 10, cx, cy - 4)
        heart.cubicTo(cx + 9, cy - 10, cx + 10, cy - 1, cx, cy + 7)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_qcolor("#FFFFFF", 230))
        painter.drawPath(heart)

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
            if self.is_sub_card and self.parent_group is not None:
                self.board.update_sub_card_order(self.parent_group, self)
            else:
                self.board.check_card_proximity(self)
                self.board.update_main_card_order(self)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self.board.finish_drag(self)
                self.end_drag()
            elif self.is_group:
                self.board.toggle_group(self)
            self._press_pos = None
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if self.is_group:
            event.accept()
            return
        self.board.show_person_menu(self, event.globalPos())
        event.accept()


class MergeConfirmDialog(QDialog):
    def __init__(self, people_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent.window() if parent is not None else None)
        self._people_count = max(2, int(people_count))
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.addStretch(1)

        self._panel = QFrame(self)
        self._panel.setFixedWidth(356)
        self._panel.setStyleSheet(
            """
            QFrame {
                background: rgba(255, 255, 255, 0.94);
                border: 1px solid rgba(255, 255, 255, 0.65);
                border-radius: 28px;
            }
            """
        )
        panel_shadow = QGraphicsDropShadowEffect(self._panel)
        panel_shadow.setBlurRadius(40)
        panel_shadow.setOffset(0, 12)
        panel_shadow.setColor(QColor(0, 0, 0, 46))
        self._panel.setGraphicsEffect(panel_shadow)

        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(22, 22, 22, 18)
        panel_layout.setSpacing(16)

        text_width = self._panel.width() - 44

        title_label = QLabel(f"Merge All Photos of These\n{self._people_count} People?")
        title_label.setWordWrap(True)
        title_label.setTextFormat(Qt.TextFormat.PlainText)
        title_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        title_label.setFixedWidth(text_width)
        title_font = QFont("Segoe UI", 17, QFont.Weight.Bold)
        title_label.setFont(title_font)
        title_label.setMinimumHeight(max(56, title_label.heightForWidth(text_width)))
        title_label.setStyleSheet("color: #111111; background: transparent;")

        body_label = QLabel(
            f"By merging photos of these {self._people_count} people, "
            "they will be recognized as the same person."
        )
        body_label.setWordWrap(True)
        body_label.setTextFormat(Qt.TextFormat.PlainText)
        body_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        body_label.setFixedWidth(text_width)
        body_font = QFont("Segoe UI", 14, QFont.Weight.Medium)
        body_label.setFont(body_font)
        body_label.setMinimumHeight(max(46, body_label.heightForWidth(text_width)))
        body_label.setStyleSheet("color: rgba(17, 17, 17, 0.84); background: transparent;")

        merge_button = QPushButton("Merge Photos")
        merge_button.setCursor(Qt.CursorShape.PointingHandCursor)
        merge_button.setFixedHeight(42)
        merge_button.setStyleSheet(
            """
            QPushButton {
                background: #0A84FF;
                color: white;
                border: none;
                border-radius: 21px;
                font-size: 16px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #2A95FF;
            }
            QPushButton:pressed {
                background: #006BE3;
            }
            """
        )

        cancel_button = QPushButton("Cancel")
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_button.setFixedHeight(40)
        cancel_button.setStyleSheet(
            """
            QPushButton {
                background: rgba(243, 243, 244, 0.98);
                color: #2E2E2E;
                border: none;
                border-radius: 20px;
                font-size: 15px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: rgba(235, 235, 236, 0.98);
            }
            QPushButton:pressed {
                background: rgba(224, 224, 226, 0.98);
            }
            """
        )

        merge_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)

        panel_layout.addWidget(title_label)
        panel_layout.addWidget(body_label)
        panel_layout.addSpacing(2)
        panel_layout.addWidget(merge_button)
        panel_layout.addWidget(cancel_button)

        root.addWidget(self._panel, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addStretch(1)

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        window = parent.window()
        top_left = window.mapToGlobal(QPoint(0, 0))
        self.setGeometry(top_left.x(), top_left.y(), window.width(), window.height())

    def showEvent(self, event) -> None:  # noqa: N802
        self._sync_geometry()
        super().showEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(22, 24, 29, 78))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._panel.geometry().contains(event.position().toPoint()):
            self.reject()
            event.accept()
            return
        super().mousePressEvent(event)

    @classmethod
    def confirm(cls, people_count: int, parent: QWidget | None = None) -> bool:
        dialog = cls(people_count, parent)
        return dialog.exec() == QDialog.DialogCode.Accepted


class PeopleBoard(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PeopleBoard")
        self.top_cards: list[PeopleCard] = []
        self.proximity_pair: tuple[PeopleCard, PeopleCard] | None = None
        self.frame_visible = False
        self._active_anim: QParallelAnimationGroup | None = None
        self._restore_after_drag: list[PeopleCard] = []

        self.merge_frame = HintFrame(
            self,
            """
            QFrame {
                border: 3px dashed #2272F2;
                background: rgba(34, 114, 242, 0.10);
                border-radius: 30px;
            }
            """,
        )

        self.setStyleSheet("#PeopleBoard { background: transparent; }")
        self.setMinimumHeight(260)

    def add_card(self, card: PeopleCard) -> None:
        card.setParent(self)
        card.show()
        self.top_cards.append(card)
        self.update_positions()

    def visible_top_cards(self) -> list[PeopleCard]:
        return [card for card in self.top_cards if card.parent_group is None]

    def calculate_main_positions(self, cards: Iterable[PeopleCard] | None = None) -> list[QPoint]:
        visible = list(cards) if cards is not None else self.visible_top_cards()
        width = max(self.width(), 360)
        per_row = max(1, (width - CANVAS_MARGIN * 2 + SPACING) // (CARD_WIDTH + SPACING))

        positions: list[QPoint] = []
        x = CANVAS_MARGIN
        y = CANVAS_MARGIN
        for index, _card in enumerate(visible):
            if index > 0 and index % per_row == 0:
                x = CANVAS_MARGIN
                y += CARD_HEIGHT + SPACING
            positions.append(QPoint(int(x), int(y)))
            x += CARD_WIDTH + SPACING
        return positions

    def calculate_sub_positions(self, group: PeopleCard) -> list[QPoint]:
        width = max(self.width(), 360)
        per_row = max(1, (width - CANVAS_MARGIN * 2 + SUB_SPACING) // (CARD_WIDTH + SUB_SPACING))

        positions: list[QPoint] = []
        x = CANVAS_MARGIN
        y = group.y() + CARD_HEIGHT + SPACING
        for index, _member in enumerate(group.members):
            if index > 0 and index % per_row == 0:
                x = CANVAS_MARGIN
                y += CARD_HEIGHT + SUB_SPACING
            positions.append(QPoint(int(x), int(y)))
            x += CARD_WIDTH + SUB_SPACING
        return positions

    def calculate_layout(self) -> dict[PeopleCard, QPoint]:
        width = max(self.width(), 360)
        per_row = max(1, (width - CANVAS_MARGIN * 2 + SPACING) // (CARD_WIDTH + SPACING))

        positions: dict[PeopleCard, QPoint] = {}
        x = CANVAS_MARGIN
        y = CANVAS_MARGIN
        index = 0

        for card in self.visible_top_cards():
            if index > 0 and index % per_row == 0:
                x = CANVAS_MARGIN
                y += CARD_HEIGHT + SPACING

            positions[card] = QPoint(int(x), int(y))

            if card.is_group and card.is_expanded and card.members:
                sub_y = y + CARD_HEIGHT + SPACING
                sub_x = CANVAS_MARGIN
                sub_per_row = max(1, (width - CANVAS_MARGIN * 2 + SUB_SPACING) // (CARD_WIDTH + SUB_SPACING))
                for sub_index, member in enumerate(card.members):
                    if sub_index > 0 and sub_index % sub_per_row == 0:
                        sub_x = CANVAS_MARGIN
                        sub_y += CARD_HEIGHT + SUB_SPACING
                    positions[member] = QPoint(int(sub_x), int(sub_y))
                    sub_x += CARD_WIDTH + SUB_SPACING

                y = sub_y + CARD_HEIGHT + SPACING
                x = CANVAS_MARGIN
                index = 0
                continue

            x += CARD_WIDTH + SPACING
            index += 1

        return positions

    def update_positions(self) -> None:
        layout = self.calculate_layout()

        for card in self.visible_top_cards():
            if not card._dragging and card in layout:
                card.move(layout[card])
            if not card.is_group:
                card.show()

            if card.is_group:
                if card.is_expanded:
                    for member in card.members:
                        member.show()
                        if not member._dragging and member in layout:
                            member.move(layout[member])
                else:
                    for member in card.members:
                        member.hide()
                self._update_group_background(card, layout)

        max_bottom = CANVAS_MARGIN
        if layout:
            max_bottom = max(point.y() for point in layout.values()) + CARD_HEIGHT + CANVAS_MARGIN
        self.setMinimumHeight(max_bottom)

    def _update_group_background(self, group: PeopleCard, layout: dict[PeopleCard, QPoint]) -> None:
        background = group.ensure_background()
        if not group.is_expanded or not group.members:
            background.hide()
            return

        points = [layout[member] for member in group.members if member in layout]
        if not points:
            background.hide()
            return

        min_x = min(point.x() for point in points)
        min_y = min(point.y() for point in points)
        max_x = max(point.x() for point in points) + CARD_WIDTH
        max_y = max(point.y() for point in points) + CARD_HEIGHT

        margin = SPACING // 2
        background.setGeometry(min_x - margin, min_y - margin, max_x - min_x + margin * 2, max_y - min_y + margin * 2)
        background.show()
        background.lower()

    def begin_drag(self, card: PeopleCard) -> None:
        if card.is_sub_card:
            return

        self._restore_after_drag = [group for group in self.top_cards if group.is_group and group.is_expanded]
        if self._restore_after_drag:
            for group in self._restore_after_drag:
                group.is_expanded = False
                for member in group.members:
                    member.hide()
                group.update()
            self.update_positions()

    def finish_drag(self, card: PeopleCard) -> None:
        if card.is_sub_card:
            if card.parent_group is not None:
                self.finalize_sub_card_order(card.parent_group)
            return

        self.check_card_proximity(card)
        expand_targets = [group for group in self._restore_after_drag if group in self.top_cards]
        merged_group = None
        if self.proximity_pair is not None:
            source, target = self.proximity_pair
            people_count = self._merge_people_count(source) + self._merge_people_count(target)
            if MergeConfirmDialog.confirm(people_count, self):
                merged_group = self.merge_cards(source, target)
                if merged_group is not None and merged_group not in expand_targets:
                    expand_targets.append(merged_group)

        self.hide_merge_frame()
        self.proximity_pair = None
        self._animate_collapsed_layout(expand_targets)

    def _animate_collapsed_layout(self, expand_targets: list[PeopleCard]) -> None:
        if self._active_anim is not None and self._active_anim.state() == QAbstractAnimation.State.Running:
            self._active_anim.stop()

        targets = self.calculate_main_positions(self.visible_top_cards())
        group = QParallelAnimationGroup(self)
        for card, target in zip(self.visible_top_cards(), targets):
            if card.pos() != target:
                group.addAnimation(_create_pos_anim(card, target))

        def finalize() -> None:
            self._active_anim = None
            self.restore_groups(expand_targets)

        if group.animationCount() == 0:
            finalize()
            return

        group.finished.connect(finalize)
        self._active_anim = group
        group.start()

    def restore_groups(self, groups: list[PeopleCard]) -> None:
        for group in groups:
            if group in self.top_cards and group.is_group:
                group.is_expanded = True
                for member in group.members:
                    member.show()
                group.update()
        self._restore_after_drag = []
        self.update_positions()

    def toggle_group(self, group: PeopleCard) -> None:
        if group not in self.top_cards:
            return
        group.is_expanded = not group.is_expanded
        for member in group.members:
            member.setVisible(group.is_expanded)
        group.update()
        self.update_positions()

    def update_main_card_order(self, dragged: PeopleCard) -> None:
        if dragged.is_sub_card:
            return

        visible = self.visible_top_cards()
        targets = self.calculate_main_positions(visible)
        if not targets:
            return

        center = dragged.pos() + QPoint(CARD_WIDTH // 2, CARD_HEIGHT // 2)
        closest_index = min(
            range(len(targets)),
            key=lambda index: (targets[index] + QPoint(CARD_WIDTH // 2, CARD_HEIGHT // 2) - center).manhattanLength(),
        )

        if dragged in self.top_cards:
            self.top_cards.remove(dragged)
        new_order = self.top_cards[:]
        new_order.insert(closest_index, dragged)
        self.top_cards = new_order

        instant_targets = self.calculate_main_positions(self.visible_top_cards())
        for card, target in zip(self.visible_top_cards(), instant_targets):
            if card is dragged or card._dragging:
                continue
            card.move(target)

    def update_sub_card_order(self, group: PeopleCard, dragged: PeopleCard) -> None:
        targets = self.calculate_sub_positions(group)
        if not targets:
            return

        center = dragged.pos() + QPoint(CARD_WIDTH // 2, CARD_HEIGHT // 2)
        closest_index = min(
            range(len(targets)),
            key=lambda index: (targets[index] + QPoint(CARD_WIDTH // 2, CARD_HEIGHT // 2) - center).manhattanLength(),
        )

        if dragged in group.members:
            group.members.remove(dragged)
        group.members.insert(closest_index, dragged)
        group.update()

        final_targets = self.calculate_sub_positions(group)
        for member, target in zip(group.members, final_targets):
            if member is dragged or member._dragging:
                continue
            member.move(target)

        self._update_group_background(group, self.calculate_layout())

    def finalize_sub_card_order(self, group: PeopleCard) -> None:
        targets = self.calculate_sub_positions(group)
        if not targets:
            return

        if self._active_anim is not None and self._active_anim.state() == QAbstractAnimation.State.Running:
            self._active_anim.stop()

        anim_group = QParallelAnimationGroup(self)
        for member, target in zip(group.members, targets):
            if member.pos() != target:
                anim_group.addAnimation(_create_pos_anim(member, target))

        def finalize() -> None:
            self._active_anim = None
            self.update_positions()

        if anim_group.animationCount() == 0:
            finalize()
            return

        anim_group.finished.connect(finalize)
        self._active_anim = anim_group
        anim_group.start()

    def check_card_proximity(self, dragged: PeopleCard) -> None:
        if dragged.is_sub_card:
            self.hide_merge_frame()
            self.proximity_pair = None
            return

        candidates = [card for card in self.visible_top_cards() if card is not dragged]
        if not candidates:
            self.hide_merge_frame()
            self.proximity_pair = None
            return

        closest = min(candidates, key=lambda candidate: _button_distance(dragged, candidate))
        distance = _button_distance(dragged, closest)
        if distance < PROXIMITY_THRESHOLD:
            self.show_merge_frame(dragged, closest)
            self.proximity_pair = (dragged, closest)
        else:
            self.hide_merge_frame()
            self.proximity_pair = None

    def show_merge_frame(self, card1: PeopleCard, card2: PeopleCard) -> None:
        left = min(card1.x(), card2.x()) - 10
        top = min(card1.y(), card2.y()) - 10
        right = max(card1.x() + CARD_WIDTH, card2.x() + CARD_WIDTH) + 10
        bottom = max(card1.y() + CARD_HEIGHT, card2.y() + CARD_HEIGHT) + 10
        self.merge_frame.setGeometry(left, top, right - left, bottom - top)
        self.merge_frame.show()
        self.merge_frame.raise_()
        self.frame_visible = True

    def hide_merge_frame(self) -> None:
        self.merge_frame.hide()
        self.frame_visible = False

    def merge_cards(self, source: PeopleCard, target: PeopleCard) -> PeopleCard | None:
        if source is target:
            return None

        if source.is_group and target.is_group:
            for member in list(source.members):
                member.parent_group = target
                target.members.append(member)
            source.members.clear()
            self._remove_top_card(source)
            self._cleanup_group_background(source)
            source.hide()
            source.deleteLater()
            target.update()
            return target

        if source.is_group and not target.is_group:
            self._add_person_to_group(target, source)
            return source

        if target.is_group and not source.is_group:
            self._add_person_to_group(source, target)
            return target

        source_index = self.top_cards.index(source)
        target_index = self.top_cards.index(target)
        insert_index = min(source_index, target_index)
        ordered_members = [source, target] if source_index < target_index else [target, source]

        group = PeopleCard(board=self, group=True, seed_index=min(source.seed_index, target.seed_index), parent=self)
        group.members = []
        group.show()

        for member in ordered_members:
            member.is_sub_card = True
            member.parent_group = group
            group.members.append(member)
            member.hide()

        self._remove_top_card(source)
        self._remove_top_card(target)
        self.top_cards.insert(insert_index, group)
        group.move(source.pos())
        group.update()
        return group

    def _add_person_to_group(self, person: PeopleCard, group: PeopleCard) -> None:
        if person in self.top_cards:
            self.top_cards.remove(person)
        person.is_sub_card = True
        person.parent_group = group
        person.hide()
        group.members.append(person)
        group.update()

    def _remove_top_card(self, card: PeopleCard) -> None:
        if card in self.top_cards:
            self.top_cards.remove(card)

    def _cleanup_group_background(self, group: PeopleCard) -> None:
        if group._background is not None:
            group._background.hide()
            group._background.deleteLater()
            group._background = None

    def _merge_people_count(self, card: PeopleCard) -> int:
        if card.is_group:
            return len(card.members)
        return 1

    def show_person_menu(self, card: PeopleCard, global_pos) -> None:
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        menu.setStyleSheet(MENU_STYLE)
        rename_label = "Rename" if card.person_name else "Name This Person"
        rename_action = QAction(rename_label, menu)
        hide_action = QAction("Hide This Person", menu)

        rename_action.triggered.connect(lambda: self.rename_person(card))
        hide_action.triggered.connect(lambda: self.hide_person(card))

        menu.addAction(rename_action)
        menu.addSeparator()
        menu.addAction(hide_action)
        menu.exec(global_pos)

    def rename_person(self, card: PeopleCard) -> None:
        title = "Rename Person" if card.person_name else "Name This Person"
        current_text = card.person_name or ""
        text, accepted = QInputDialog.getText(self, title, "Name:", text=current_text)
        if not accepted:
            return
        card.set_name(text)

    def hide_person(self, card: PeopleCard) -> None:
        if card.is_sub_card and card.parent_group is not None:
            group = card.parent_group
            if card in group.members:
                group.members.remove(card)
            card.parent_group = None
            card.is_sub_card = False
            card.hide()
            card.deleteLater()
            group.update()
            self._dissolve_group_if_needed(group)
        else:
            self._remove_top_card(card)
            card.hide()
            card.deleteLater()

        self.update_positions()

    def _dissolve_group_if_needed(self, group: PeopleCard) -> None:
        if len(group.members) >= 2:
            return

        group_index = self.top_cards.index(group) if group in self.top_cards else len(self.top_cards)
        survivors = list(group.members)
        self._remove_top_card(group)
        self._cleanup_group_background(group)
        group.hide()
        group.deleteLater()

        for offset, survivor in enumerate(survivors):
            survivor.is_sub_card = False
            survivor.parent_group = None
            survivor.show()
            self.top_cards.insert(group_index + offset, survivor)

    def sort_cards(self, *, named_first: bool) -> None:
        def key(card: PeopleCard) -> tuple[int, str, int]:
            if named_first:
                return card.sort_key()
            name = (card.display_name() or "").strip().lower()
            return 0, name, card.seed_index

        self.top_cards.sort(key=key)
        self.update_positions()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.update_positions()


class PeopleWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("People Demo")
        self.resize(1260, 860)

        central = QWidget()
        central.setObjectName("PeopleWindowRoot")
        central.setStyleSheet("#PeopleWindowRoot { background: #F5F5F7; }")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        title = QLabel("People & Pets")
        title.setStyleSheet("color: #111111; font-size: 18px; font-weight: 700;")

        self.sort_button = QToolButton()
        self.sort_button.setText("Sort")
        self.sort_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sort_button.setStyleSheet(
            """
            QToolButton {
                border: none;
                color: #356CB4;
                font-size: 15px;
                font-weight: 600;
                padding: 6px 10px;
            }
            QToolButton::menu-indicator { image: none; width: 0; }
            """
        )

        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.sort_button)
        root.addLayout(header)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("PeopleScrollArea")
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("#PeopleScrollArea { background: transparent; border: none; }")
        root.addWidget(self.scroll_area, 1)

        self.board = PeopleBoard()
        self.scroll_area.setWidget(self.board)

        for index, (name, palette) in enumerate(SAMPLE_PEOPLE):
            card = PeopleCard(board=self.board, palette=palette, name=name, seed_index=index, parent=self.board)
            self.board.add_card(card)

        self._build_sort_menu()

    def _build_sort_menu(self) -> None:
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        menu.setStyleSheet(MENU_STYLE)
        name_action = QAction("A-Z", menu)
        named_action = QAction("Named First", menu)
        name_action.triggered.connect(lambda: self.board.sort_cards(named_first=False))
        named_action.triggered.connect(lambda: self.board.sort_cards(named_first=True))
        menu.addAction(name_action)
        menu.addAction(named_action)
        self.sort_button.setMenu(menu)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PeopleWindow()
    window.show()
    sys.exit(app.exec())
