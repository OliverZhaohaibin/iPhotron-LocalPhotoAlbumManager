"""People dashboard page with the demo-style board layout."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPoint,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from iPhoto.people.image_utils import create_cover_thumbnail, load_image_rgb
from iPhoto.people.repository import PersonSummary
from iPhoto.people.service import PeopleService


CARD_WIDTH = 156
CARD_HEIGHT = 212
CARD_RADIUS = 24
SPACING = 18
PROXIMITY_THRESHOLD = 120
CANVAS_MARGIN = 18
PLACEHOLDER_BACKDROPS: tuple[tuple[str, str], ...] = (
    ("#5A7C6A", "#20352C"),
    ("#A54C53", "#3C2024"),
    ("#C69B6E", "#6A4427"),
    ("#668B6E", "#25352B"),
    ("#5D677A", "#232A35"),
    ("#A9B8C9", "#415166"),
)

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


def _button_distance(first: QWidget, second: QWidget) -> float:
    c1 = first.pos() + QPoint(CARD_WIDTH // 2, CARD_HEIGHT // 2)
    c2 = second.pos() + QPoint(CARD_WIDTH // 2, CARD_HEIGHT // 2)
    return math.hypot(c1.x() - c2.x(), c1.y() - c2.y())


class HintFrame(QFrame):
    def __init__(self, parent: QWidget, style_sheet: str) -> None:
        super().__init__(parent)
        self.setStyleSheet(style_sheet)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()


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
        if self._artwork is None:
            self._artwork = self._render_cover_art()
        return self._artwork

    def _render_cover_art(self) -> QPixmap:
        thumbnail_path = self.summary.thumbnail_path
        if thumbnail_path is not None and thumbnail_path.exists():
            try:
                image = load_image_rgb(thumbnail_path)
                cover = create_cover_thumbnail(image, (CARD_WIDTH * 2, CARD_HEIGHT * 2))
                data = cover.tobytes("raw", "RGBA")
                qimage = QImage(
                    data,
                    CARD_WIDTH * 2,
                    CARD_HEIGHT * 2,
                    CARD_WIDTH * 8,
                    QImage.Format.Format_RGBA8888,
                ).copy()
                return QPixmap.fromImage(qimage)
            except Exception:
                pass
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

        border_color = QColor("#2272F2") if (self._hovered or self._dragging) else QColor(255, 255, 255, 110)
        border_width = 3.0 if (self._hovered or self._dragging) else 1.2
        painter.setPen(QPen(border_color, border_width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(card_rect, CARD_RADIUS, CARD_RADIUS)

    def _paint_bottom_overlay(self, painter: QPainter, card_rect: QRectF) -> None:
        gradient = QLinearGradient(card_rect.left(), card_rect.bottom() - 82, card_rect.left(), card_rect.bottom())
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


class PeopleBoard(QWidget):
    mergeRequested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PeopleBoard")
        self.top_cards: list[PeopleCard] = []
        self.proximity_pair: tuple[PeopleCard, PeopleCard] | None = None
        self._active_anim: QParallelAnimationGroup | None = None

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

    def set_cards(self, cards: list[PeopleCard]) -> None:
        self.clear_cards()
        self.top_cards = list(cards)
        for card in self.top_cards:
            card.setParent(self)
            card.show()
        self.update_positions()

    def clear_cards(self) -> None:
        if self._active_anim is not None and self._active_anim.state() == QAbstractAnimation.State.Running:
            self._active_anim.stop()
        for card in self.top_cards:
            card.hide()
            card.deleteLater()
        self.top_cards = []
        self.merge_frame.hide()
        self.proximity_pair = None
        self.setMinimumHeight(260)

    def visible_cards(self) -> list[PeopleCard]:
        return list(self.top_cards)

    def calculate_positions(self, cards: list[PeopleCard] | None = None) -> list[QPoint]:
        visible = list(cards) if cards is not None else self.visible_cards()
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

    def update_positions(self) -> None:
        positions = self.calculate_positions()
        for card, position in zip(self.visible_cards(), positions):
            if not card._dragging:
                card.move(position)
            card.show()

        max_bottom = CANVAS_MARGIN
        if positions:
            max_bottom = max(point.y() for point in positions) + CARD_HEIGHT + CANVAS_MARGIN
        self.setMinimumHeight(max_bottom)

    def begin_drag(self, card: PeopleCard) -> None:
        del card
        if self._active_anim is not None and self._active_anim.state() == QAbstractAnimation.State.Running:
            self._active_anim.stop()

    def finish_drag(self, card: PeopleCard) -> None:
        self.check_card_proximity(card)
        if self.proximity_pair is not None:
            source, target = self.proximity_pair
            if MergeConfirmDialog.confirm(2, self):
                self.mergeRequested.emit(source.person_id, target.person_id)

        self.hide_merge_frame()
        self.proximity_pair = None
        self.animate_to_layout()

    def animate_to_layout(self) -> None:
        if self._active_anim is not None and self._active_anim.state() == QAbstractAnimation.State.Running:
            self._active_anim.stop()

        targets = self.calculate_positions(self.visible_cards())
        group = QParallelAnimationGroup(self)
        for card, target in zip(self.visible_cards(), targets):
            if card.pos() != target:
                group.addAnimation(_create_pos_anim(card, target))

        def finalize() -> None:
            self._active_anim = None
            self.update_positions()

        if group.animationCount() == 0:
            finalize()
            return

        group.finished.connect(finalize)
        self._active_anim = group
        group.start()

    def update_card_order(self, dragged: PeopleCard) -> None:
        visible = self.visible_cards()
        targets = self.calculate_positions(visible)
        if not targets:
            return

        center = dragged.pos() + QPoint(CARD_WIDTH // 2, CARD_HEIGHT // 2)
        closest_index = min(
            range(len(targets)),
            key=lambda index: (
                targets[index] + QPoint(CARD_WIDTH // 2, CARD_HEIGHT // 2) - center
            ).manhattanLength(),
        )

        if dragged in self.top_cards:
            self.top_cards.remove(dragged)
        self.top_cards.insert(closest_index, dragged)

        instant_targets = self.calculate_positions(self.visible_cards())
        for card, target in zip(self.visible_cards(), instant_targets):
            if card is dragged or card._dragging:
                continue
            card.move(target)

    def check_card_proximity(self, dragged: PeopleCard) -> None:
        candidates = [card for card in self.visible_cards() if card is not dragged]
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

    def show_merge_frame(self, first: PeopleCard, second: PeopleCard) -> None:
        left = min(first.x(), second.x()) - 10
        top = min(first.y(), second.y()) - 10
        right = max(first.x() + CARD_WIDTH, second.x() + CARD_WIDTH) + 10
        bottom = max(first.y() + CARD_HEIGHT, second.y() + CARD_HEIGHT) + 10
        self.merge_frame.setGeometry(left, top, right - left, bottom - top)
        self.merge_frame.show()
        self.merge_frame.raise_()

    def hide_merge_frame(self) -> None:
        self.merge_frame.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.update_positions()


class PeopleDashboardWidget(QWidget):
    clusterActivated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = PeopleService()
        self._status_message: str | None = None
        self._summaries: list[PersonSummary] = []
        self._cards: dict[str, PeopleCard] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        self._title = QLabel("People")
        self._title.setStyleSheet("color: #111111; font-size: 18px; font-weight: 700;")

        self._refresh_button = QToolButton()
        self._refresh_button.setText("Refresh")
        self._refresh_button.setAutoRaise(True)
        self._refresh_button.setStyleSheet(
            """
            QToolButton {
                border: none;
                color: #356CB4;
                font-size: 15px;
                font-weight: 600;
                padding: 6px 10px;
            }
            """
        )
        self._refresh_button.clicked.connect(self.reload)

        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._refresh_button)
        root.addLayout(header)

        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.setStyleSheet("color: #63739A; font-size: 13px;")
        root.addWidget(self._message)

        self._empty = QLabel()
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(
            """
            QLabel {
                padding: 32px;
                border: 1px dashed rgba(16, 24, 40, 0.14);
                border-radius: 24px;
                color: #667085;
                background: rgba(255, 255, 255, 0.72);
            }
            """
        )
        root.addWidget(self._empty)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("PeopleScrollArea")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("#PeopleScrollArea { background: transparent; border: none; }")
        self._scroll.hide()
        root.addWidget(self._scroll, 1)

        self._board = PeopleBoard()
        self._board.mergeRequested.connect(self._merge_cluster_pair)
        self._scroll.setWidget(self._board)

    def set_library_root(self, library_root: Path | None) -> None:
        self._service.set_library_root(library_root)
        self.reload()

    def build_cluster_query(self, person_id: str):
        return self._service.build_cluster_query(person_id)

    def set_status_message(self, message: str | None) -> None:
        self._status_message = message or None
        self.reload()

    def reload(self) -> None:
        self._clear_cards()
        if not self._service.is_bound():
            self._message.setText("Bind a Basic Library to see People clusters.")
            self._empty.setText("People appears here after a library is bound and scanned.")
            self._empty.show()
            self._scroll.hide()
            return

        self._summaries = self._service.list_clusters()
        counts = self._service.face_status_counts()
        pending = counts.get("pending", 0) + counts.get("retry", 0)

        if self._summaries:
            self._message.setText(
                "Click a cluster card to open its assets, or drag cards close together to merge clusters."
            )
            self._empty.hide()
            self._scroll.show()
            self._populate_cards()
            return

        if self._status_message:
            body = self._status_message
        elif pending > 0:
            body = "Scanning faces in the background. This page will fill in as clusters are ready."
        else:
            body = "No People clusters yet. Run a scan to build face groups."
        self._message.setText(body)
        self._empty.setText(body)
        self._empty.show()
        self._scroll.hide()

    def _populate_cards(self) -> None:
        cards: list[PeopleCard] = []
        for index, summary in enumerate(self._summaries):
            card = PeopleCard(
                board=self._board,
                summary=summary,
                seed_index=index,
            )
            card.activated.connect(self.clusterActivated.emit)
            card.menuRequested.connect(self._show_card_menu)
            self._cards[summary.person_id] = card
            cards.append(card)
        self._board.set_cards(cards)

    def _clear_cards(self) -> None:
        self._board.clear_cards()
        self._cards.clear()

    def _summary_for_person(self, person_id: str) -> PersonSummary | None:
        return next((item for item in self._summaries if item.person_id == person_id), None)

    def _show_card_menu(self, person_id: str, global_pos) -> None:
        summary = self._summary_for_person(person_id)
        if summary is None:
            return

        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        menu.setStyleSheet(MENU_STYLE)
        rename_text = "Rename" if summary.name else "Name This Person"
        rename_action = QAction(rename_text, menu)
        merge_action = QAction("Merge Into...", menu)
        merge_action.setEnabled(len(self._summaries) > 1)
        rename_action.triggered.connect(lambda: self._rename_person(summary))
        merge_action.triggered.connect(lambda: self._merge_person(summary))
        menu.addAction(rename_action)
        menu.addSeparator()
        menu.addAction(merge_action)
        menu.exec(global_pos)

    def _rename_person(self, summary: PersonSummary) -> None:
        title = "Rename Person" if summary.name else "Name This Person"
        text, accepted = QInputDialog.getText(self, title, "Name:", text=summary.name or "")
        if not accepted:
            return
        self._service.rename_cluster(summary.person_id, text.strip() or None)
        self.reload()

    def _merge_person(self, summary: PersonSummary) -> None:
        choices = [
            (f"{target.name or 'Unnamed'} ({target.face_count} faces)", target.person_id)
            for target in self._summaries
            if target.person_id != summary.person_id
        ]
        if not choices:
            return

        labels = [label for label, _ in choices]
        selected, accepted = QInputDialog.getItem(
            self,
            "Merge Person",
            "Merge into:",
            labels,
            editable=False,
        )
        if not accepted:
            return
        selected_id = next((person_id for label, person_id in choices if label == selected), None)
        if selected_id is None:
            return
        self._confirm_merge(summary.person_id, selected_id)

    def _merge_cluster_pair(self, source_person_id: str, target_person_id: str) -> None:
        self._confirm_merge(source_person_id, target_person_id)

    def _confirm_merge(self, source_person_id: str, target_person_id: str) -> bool:
        if source_person_id == target_person_id:
            return False

        source = self._summary_for_person(source_person_id)
        target = self._summary_for_person(target_person_id)
        if source is None or target is None:
            return False

        if not MergeConfirmDialog.confirm(2, self):
            return False

        merged = self._service.merge_clusters(source_person_id, target_person_id)
        if merged:
            self.reload()
        return merged
