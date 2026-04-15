"""Drag/reorder board for People cluster cards."""

from __future__ import annotations

from PySide6.QtCore import QAbstractAnimation, QPoint, QParallelAnimationGroup, Signal
from PySide6.QtWidgets import QWidget

from .people_dashboard_cards import PeopleCard
from .people_dashboard_shared import (
    CANVAS_MARGIN,
    CARD_HEIGHT,
    CARD_WIDTH,
    PROXIMITY_THRESHOLD,
    SPACING,
    HintFrame,
    _button_distance,
    _create_pos_anim,
)


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
        if (
            self._active_anim is not None
            and self._active_anim.state() == QAbstractAnimation.State.Running
        ):
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
            if not card.is_dragging:
                card.move(position)
            card.show()

        max_bottom = CANVAS_MARGIN
        if positions:
            max_bottom = max(point.y() for point in positions) + CARD_HEIGHT + CANVAS_MARGIN
        self.setMinimumHeight(max_bottom)

    def begin_drag(self, card: PeopleCard) -> None:
        del card
        if (
            self._active_anim is not None
            and self._active_anim.state() == QAbstractAnimation.State.Running
        ):
            self._active_anim.stop()

    def finish_drag(self, card: PeopleCard) -> None:
        self.check_card_proximity(card)
        if self.proximity_pair is not None:
            source, target = self.proximity_pair
            self.mergeRequested.emit(source.person_id, target.person_id)

        self.hide_merge_frame()
        self.proximity_pair = None
        self.animate_to_layout()

    def animate_to_layout(self) -> None:
        if (
            self._active_anim is not None
            and self._active_anim.state() == QAbstractAnimation.State.Running
        ):
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
