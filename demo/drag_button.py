#!/usr/bin/env python3
"""
Standalone demo that reproduces the LexiMemo word-book button
drag / reorder / merge / folder-expand / sub-button-split interactions.

Run:
    python wordbook_button_demo.py

Dependencies: PySide6 only.
Button visuals use the default Qt style (no custom icons / colours).
"""
from __future__ import annotations

import math
import sys
from typing import Dict, List, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    Property,
    QAbstractAnimation,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyleOptionButton,
    QWidget,
)

# ──────────────────────────────────────────────
# Constants (same as the real app)
# ──────────────────────────────────────────────
BUTTON_WIDTH = 120
BUTTON_HEIGHT = 150
SPACING = 20
TOP_MARGIN = 60
PROXIMITY_THRESHOLD = 100

# ──────────────────────────────────────────────
# Helper: distance between two button centres
# ──────────────────────────────────────────────

def _button_distance(btn1: QWidget, btn2: QWidget) -> float:
    c1 = btn1.pos() + QPoint(BUTTON_WIDTH // 2, BUTTON_HEIGHT // 2)
    c2 = btn2.pos() + QPoint(BUTTON_WIDTH // 2, BUTTON_HEIGHT // 2)
    dx = c1.x() - c2.x()
    dy = c1.y() - c2.y()
    return math.sqrt(dx * dx + dy * dy)


# ──────────────────────────────────────────────
# Hint Frames (merge / reorder / removal)
# ──────────────────────────────────────────────

class HintFrame(QFrame):
    def __init__(self, parent: QWidget, style_sheet: str) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Box)
        self.setLineWidth(2)
        self.setStyleSheet(style_sheet)
        self.hide()


class FolderBackground(QFrame):
    """Grey dashed background behind expanded folder sub-buttons."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "background-color: rgba(240,240,240,0.5); border: 2px dashed #888888;"
        )
        self.setFrameShape(QFrame.Box)
        self.lower()
        self.hide()


# ──────────────────────────────────────────────
# DemoButton – a QPushButton with drag / jitter
# ──────────────────────────────────────────────

class DemoButton(QPushButton):
    """Minimal word-book-style button: jitter, drag, folder state."""

    # Class-level shared jitter timer (same optimisation as the real app)
    _shared_jitter_timer: QTimer | None = None
    _jittering_buttons: set[DemoButton] = set()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.setFixedSize(BUTTON_WIDTH, BUTTON_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)

        # Public state flags
        self.app: DemoContent | None = None
        self.is_folder: bool = False
        self.is_expanded: bool = False
        self.is_sub_button: bool = False
        self.parent_folder: DemoButton | None = None
        self.sub_buttons: list[DemoButton] = []
        self.drag_out_threshold_exceeded: bool = False
        self.is_dragging: bool = False

        # Private drag / jitter state
        self._edit_mode: bool = False
        self._drag_offset: QPoint | None = None
        self._dragging: bool = False
        self._collapsed_for_drag: bool = False

        self._jitter_phase: float = 0.0
        self._jitter_step: float = 0.5
        self._jitter_amplitude: float = 2.0
        self._rotation: float = 0.0

        # Long-press darken
        self._dark_opacity: float = 0.0
        self._fade_anim: QPropertyAnimation | None = None
        self._long_press_timer = QTimer(self, singleShot=True, interval=110)
        self._long_press_timer.timeout.connect(self._on_long_press)

        # Delete button (✕)
        self.delete_btn = QPushButton("✕", self)
        self.delete_btn.setFixedSize(22, 22)
        self.delete_btn.setStyleSheet(
            "QPushButton{background:#FF4D4D;color:#FFF;border:none;border-radius:11px;font-weight:bold;}"
            "QPushButton:hover{background:#FF8080;}"
        )
        self.delete_btn.hide()
        self.delete_btn.clicked.connect(self._on_delete_clicked)

        # Folder animation group reference
        self.folder_animation_group: QParallelAnimationGroup | None = None
        self.background_frame: FolderBackground | None = None
        self.follow_timer: QTimer | None = None

        self.destroyed.connect(self._handle_destroyed)

    # ─── Delete ────────────────────────────────
    def _on_delete_clicked(self) -> None:
        if not self.app:
            return
        self.app.delete_button(self)

    # ─── Jitter (shared timer, identical to real app) ────
    @classmethod
    def _on_shared_jitter_timeout(cls) -> None:
        for btn in list(cls._jittering_buttons):
            try:
                btn._advance_jitter()
            except RuntimeError:
                cls._jittering_buttons.discard(btn)
        if cls._shared_jitter_timer and not cls._jittering_buttons:
            cls._shared_jitter_timer.stop()
            cls._shared_jitter_timer.deleteLater()
            cls._shared_jitter_timer = None

    def _handle_destroyed(self) -> None:
        DemoButton._jittering_buttons.discard(self)
        if DemoButton._shared_jitter_timer and not DemoButton._jittering_buttons:
            DemoButton._shared_jitter_timer.stop()
            DemoButton._shared_jitter_timer.deleteLater()
            DemoButton._shared_jitter_timer = None

    def _advance_jitter(self) -> None:
        self._jitter_phase += self._jitter_step
        if self._jitter_phase >= 2 * math.pi:
            self._jitter_phase -= 2 * math.pi
        self.rotation = math.sin(self._jitter_phase) * self._jitter_amplitude

    def start_jitter(self) -> None:
        if self in DemoButton._jittering_buttons:
            return
        self._edit_mode = True
        self._jitter_phase = 0.0
        DemoButton._jittering_buttons.add(self)
        if DemoButton._shared_jitter_timer is None:
            DemoButton._shared_jitter_timer = QTimer(interval=16)
            DemoButton._shared_jitter_timer.timeout.connect(
                DemoButton._on_shared_jitter_timeout
            )
        if not DemoButton._shared_jitter_timer.isActive():
            DemoButton._shared_jitter_timer.start()
        self._update_delete_btn()

    def stop_jitter(self) -> None:
        if self in DemoButton._jittering_buttons:
            DemoButton._jittering_buttons.remove(self)
        if DemoButton._shared_jitter_timer and not DemoButton._jittering_buttons:
            DemoButton._shared_jitter_timer.stop()
            DemoButton._shared_jitter_timer.deleteLater()
            DemoButton._shared_jitter_timer = None
        self.rotation = 0.0
        self._edit_mode = False
        self._update_delete_btn()

    # rotation property for jitter / paint
    def _get_rotation(self) -> float:
        return self._rotation

    def _set_rotation(self, v: float) -> None:
        self._rotation = v
        self.update()

    rotation = Property(float, _get_rotation, _set_rotation)

    # ─── Mouse interaction (identical feel) ─────
    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._set_dark(1.0)
            self._long_press_timer.start()
            if self._edit_mode:
                self._drag_offset = ev.pos()
                self._dragging = False
                self.is_dragging = False
                self.raise_()
                self._collapsed_for_drag = False
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._long_press_timer.stop()
            self._fade_dark()
            if self._edit_mode and self._dragging:
                self._dragging = False
                self.is_dragging = False
                if self.app:
                    if self.is_sub_button and self.parent_folder:
                        if not self.drag_out_threshold_exceeded:
                            self.app.update_sub_button_order(
                                self.parent_folder, dragged_sub_button=self, realtime=False
                            )
                        else:
                            self.app.remove_sub_button_from_folder(self)
                        self.app.hide_blue_reorder_frame()
                        self.app.hide_red_removal_frame()
                    else:
                        if self.app.frame_visible and self.app.is_button_in_frame(self):
                            self.app.merge_folders()
                        if not self._collapsed_for_drag:
                            self.app.finalize_button_order()
                        self.app.hide_frame()
            elif not self._edit_mode and self.rect().contains(ev.pos()):
                if self.is_folder and self.app:
                    self.app.toggle_folder(self)
        if self._collapsed_for_drag:
            self._expand_after_drag()
        super().mouseReleaseEvent(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._edit_mode and ev.buttons() & Qt.LeftButton and self._drag_offset is not None:
            if not self._dragging and (ev.pos() - self._drag_offset).manhattanLength() > 3:
                self._dragging = True
                self.is_dragging = True
                if (
                    not self.is_sub_button
                    and self.app
                    and hasattr(self.app, "collapse_all_folders")
                    and not self._collapsed_for_drag
                ):
                    try:
                        self.app.collapse_all_folders(skip_buttons=[self])
                        self._collapsed_for_drag = True
                    except Exception:
                        self._collapsed_for_drag = False
            if self._dragging:
                new_pos = self.mapToParent(ev.pos() - self._drag_offset)
                self.move(new_pos)
                if self.app:
                    if self.is_sub_button and self.parent_folder:
                        reorder_rect = _calculate_reorder_area(
                            self.parent_folder,
                            BUTTON_WIDTH,
                            BUTTON_HEIGHT,
                            SPACING,
                            self.app.scroll_content.width(),
                            0,
                            dragging_btn=self,
                        )
                        center = self.mapTo(self.app, self.rect().center())
                        if reorder_rect.contains(center):
                            self.app.show_blue_reorder_frame(self.parent_folder)
                            self.app.hide_red_removal_frame()
                            self.drag_out_threshold_exceeded = False
                            self.app.update_sub_button_order(
                                self.parent_folder, dragged_sub_button=self, realtime=True
                            )
                        else:
                            self.app.hide_blue_reorder_frame()
                            self.app.show_red_removal_frame(self.parent_folder)
                            self.drag_out_threshold_exceeded = True
                    else:
                        self.app.check_button_proximity(self)
                        self.app.update_button_order(self)
                return
        super().mouseMoveEvent(ev)

    def _expand_after_drag(self) -> None:
        if self._collapsed_for_drag and self.app:
            try:
                self.app.expand_all_folders()
            except Exception:
                pass
            try:
                self.app.finalize_button_order()
            except Exception:
                pass
            self.is_dragging = False
            self._collapsed_for_drag = False

    # ─── Long-press darken ──────────────────────
    def _on_long_press(self) -> None:
        if self.isDown():
            self._fade_dark()

    def _set_dark(self, value: float) -> None:
        self._dark_opacity = max(0.0, min(1.0, value))
        self.update()

    def _fade_dark(self) -> None:
        if self._fade_anim and self._fade_anim.state() == QPropertyAnimation.Running:
            self._fade_anim.stop()
        self._fade_anim = QPropertyAnimation(self, b"darkOpacity", self)
        self._fade_anim.setStartValue(self._dark_opacity)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setDuration(150)
        self._fade_anim.start()

    def _get_dark(self) -> float:
        return self._dark_opacity

    def _set_dark_prop(self, v: float) -> None:
        self._set_dark(v)

    darkOpacity = Property(float, _get_dark, _set_dark_prop)

    # ─── Delete button position ────────────────
    def _update_delete_btn(self) -> None:
        self.delete_btn.move(self.width() - self.delete_btn.width(), 0)
        self.delete_btn.setVisible(self._edit_mode)
        if self._edit_mode:
            self.delete_btn.raise_()

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._update_delete_btn()

    # ─── Paint (default style + rotation + darken overlay) ──
    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.save()

        if self._rotation:
            painter.translate(self.width() / 2, self.height() / 2)
            painter.rotate(self._rotation)
            painter.translate(-self.width() / 2, -self.height() / 2)

        # Draw the standard push-button chrome
        option = QStyleOptionButton()
        option.initFrom(self)
        option.state = QStyle.State_Enabled
        if self.isDown():
            option.state |= QStyle.State_Sunken
        elif not self.isFlat():
            option.state |= QStyle.State_Raised
        option.rect = self.rect()
        self.style().drawControl(QStyle.CE_PushButton, option, painter, self)

        # Text
        painter.setPen(self.palette().buttonText().color())
        fm = painter.fontMetrics()
        txt = fm.elidedText(self.text(), Qt.ElideRight, self.rect().width() - 8)
        painter.drawText(self.rect(), Qt.AlignCenter, txt)

        # Darken overlay
        if self._dark_opacity > 0.01:
            c = QColor(0, 0, 0, int(150 * self._dark_opacity))
            painter.setPen(Qt.NoPen)
            painter.setBrush(c)
            painter.drawRoundedRect(self.rect(), 4, 4)

        painter.restore()


# ──────────────────────────────────────────────
# Layout helpers (ported from _layout.py)
# ──────────────────────────────────────────────

def _calculate_main_positions(
    buttons: list[DemoButton],
    avail_w: int,
) -> list[QPoint]:
    positions: list[QPoint] = []
    x, y = SPACING, SPACING + TOP_MARGIN
    per_row = max(1, (avail_w + SPACING) // (BUTTON_WIDTH + SPACING))
    idx = 0
    for btn in buttons:
        if btn.is_sub_button:
            continue
        if idx > 0 and idx % per_row == 0:
            y += BUTTON_HEIGHT + SPACING
            x = SPACING
        positions.append(QPoint(x, y))
        x += BUTTON_WIDTH + SPACING
        idx += 1
    return positions


def _calculate_sub_positions(
    folder: DemoButton,
    avail_w: int,
) -> list[QPoint]:
    positions: list[QPoint] = []
    if not folder.sub_buttons:
        return positions
    fsp = SPACING * 1.5
    start_y = folder.y() + BUTTON_HEIGHT + SPACING
    x, y = fsp, start_y
    per_row = max(1, int((avail_w + fsp) // (BUTTON_WIDTH + fsp)))
    for idx, _sub in enumerate(folder.sub_buttons):
        if idx > 0 and idx % per_row == 0:
            y += BUTTON_HEIGHT + fsp
            x = fsp
        positions.append(QPoint(int(x), int(y)))
        x += BUTTON_WIDTH + fsp
    return positions


def _calculate_reorder_area(
    folder: DemoButton,
    bw: int,
    bh: int,
    sp: int,
    avail_w: int,
    _extra: int,
    dragging_btn: DemoButton | None = None,
) -> QRect:
    subs = [b for b in folder.sub_buttons if b is not dragging_btn]
    if not subs:
        left = 0
        top = folder.y() + bh + sp
        return QRect(left, top, avail_w, bh + sp)
    fsp = sp * 1.5
    min_y = min(b.y() for b in subs)
    max_y = max(b.y() + bh for b in subs)
    return QRect(0, int(min_y - fsp / 2), avail_w, int((max_y - min_y) + fsp))


def _calculate_folder_area(folder: DemoButton, buttons_list: list[DemoButton]) -> tuple[int, int, int, int]:
    if not buttons_list:
        return folder.x(), folder.y(), folder.x() + BUTTON_WIDTH, folder.y() + BUTTON_HEIGHT
    min_x = min(b.x() for b in buttons_list)
    min_y = min(b.y() for b in buttons_list)
    max_x = max(b.x() + BUTTON_WIDTH for b in buttons_list)
    max_y = max(b.y() + BUTTON_HEIGHT for b in buttons_list)
    return min_x, min_y, max_x, max_y


# ──────────────────────────────────────────────
# Animation helpers (ported from _animations.py)
# ──────────────────────────────────────────────

def _create_pos_anim(btn: QWidget, target: QPoint, duration: int = 200,
                     easing: QEasingCurve.Type = QEasingCurve.OutBack) -> QPropertyAnimation:
    a = QPropertyAnimation(btn, b"pos")
    a.setDuration(duration)
    a.setStartValue(btn.pos())
    a.setEndValue(target)
    a.setEasingCurve(easing)
    return a


def _create_folder_toggle_anim(
    folder: DemoButton,
    targets: list[QPoint],
    *,
    connect_finished: bool = True,
    follow_parent: bool = False,
) -> QParallelAnimationGroup:
    group = QParallelAnimationGroup()
    is_expanding = folder.is_expanded
    dur_base = 450 if is_expanding else 250
    dur_inc = 100 if is_expanding else 40
    pos_anims: list[QPropertyAnimation] = []

    for i, sub in enumerate(folder.sub_buttons):
        if is_expanding:
            sub.show()
            sub.setWindowOpacity(0)
            start = folder.pos()
            end = targets[i] if i < len(targets) else folder.pos()
        else:
            start = sub.pos()
            end = folder.pos()

        pa = QPropertyAnimation(sub, b"pos")
        pa.setDuration(dur_base + i * dur_inc)
        pa.setStartValue(start)
        pa.setEndValue(end)
        pa.setEasingCurve(QEasingCurve.OutBack if is_expanding else QEasingCurve.InBack)
        group.addAnimation(pa)
        pos_anims.append(pa)

        oa = QPropertyAnimation(sub, b"windowOpacity")
        oa.setDuration(max(dur_base - 50, dur_base))
        oa.setStartValue(0 if is_expanding else 1)
        oa.setEndValue(1 if is_expanding else 0)
        oa.setEasingCurve(QEasingCurve.InOutQuad)
        group.addAnimation(oa)

    if follow_parent and folder:
        def _sync():
            for pa in pos_anims:
                pa.setEndValue(folder.pos())

        old_t = getattr(folder, "follow_timer", None)
        if old_t:
            if old_t.isActive():
                old_t.stop()
            old_t.deleteLater()
            folder.follow_timer = None

        ft = QTimer(folder)
        ft.setInterval(30)
        ft.timeout.connect(_sync)
        ft.start()
        folder.follow_timer = ft

        def _stop_ft():
            if ft.isActive():
                ft.stop()
            ft.deleteLater()
            if getattr(folder, "follow_timer", None) is ft:
                folder.follow_timer = None

        group.finished.connect(_stop_ft)

    if connect_finished:
        if folder.app and hasattr(folder.app, "_post_folder_animation"):
            group.finished.connect(lambda: folder.app._post_folder_animation(folder))

    return group


# ──────────────────────────────────────────────
# Background animation helpers
# ──────────────────────────────────────────────

def _calc_bg_rect(folder_pos: QPoint, sub_positions: list[QPoint]) -> QRect:
    margin = SPACING // 2
    if not sub_positions:
        return QRect(folder_pos.x() - margin, folder_pos.y() - margin,
                     BUTTON_WIDTH + 2 * margin, BUTTON_HEIGHT + 2 * margin)
    min_x = min(p.x() for p in sub_positions)
    min_y = min(p.y() for p in sub_positions)
    max_x = max(p.x() for p in sub_positions) + BUTTON_WIDTH
    max_y = max(p.y() for p in sub_positions) + BUTTON_HEIGHT
    return QRect(min_x - margin, min_y - margin,
                 max_x - min_x + 2 * margin, max_y - min_y + 2 * margin)


def _update_folder_background(app: "DemoContent", folder: DemoButton) -> None:
    if not hasattr(folder, "background_frame") or folder.background_frame is None:
        folder.background_frame = FolderBackground(app.scroll_content)
        folder.background_frame.lower()
        eff = QGraphicsOpacityEffect(folder.background_frame)
        eff.setOpacity(1.0)
        folder.background_frame.setGraphicsEffect(eff)

    frame = folder.background_frame
    eff = frame.graphicsEffect()

    visible_subs = [b for b in folder.sub_buttons if b.isVisible()]
    should_show = folder.is_expanded and bool(visible_subs)

    if should_show:
        min_x = min(b.x() for b in visible_subs)
        min_y = min(b.y() for b in visible_subs)
        max_x = max(b.x() + BUTTON_WIDTH for b in visible_subs)
        max_y = max(b.y() + BUTTON_HEIGHT for b in visible_subs)
        margin = SPACING // 2
        target = QRect(min_x - margin, min_y - margin,
                       max_x - min_x + 2 * margin, max_y - min_y + 2 * margin)
        if frame.geometry() != target:
            ga = QPropertyAnimation(frame, b"geometry", frame)
            ga.setDuration(250)
            ga.setEasingCurve(QEasingCurve.OutCubic)
            ga.setStartValue(frame.geometry() if frame.isVisible() else target)
            ga.setEndValue(target)
            ga.start(QAbstractAnimation.DeleteWhenStopped)
        if frame.isHidden():
            frame.setGeometry(target)
            frame.show()
            eff.setOpacity(0.0)
            fa = QPropertyAnimation(eff, b"opacity", frame)
            fa.setDuration(250)
            fa.setEasingCurve(QEasingCurve.OutQuad)
            fa.setStartValue(0.0)
            fa.setEndValue(1.0)
            fa.start(QAbstractAnimation.DeleteWhenStopped)
    else:
        if frame.isVisible():
            fo = QPropertyAnimation(eff, b"opacity", frame)
            fo.setDuration(200)
            fo.setEasingCurve(QEasingCurve.InQuad)
            fo.setStartValue(eff.opacity())
            fo.setEndValue(0.0)

            def _hide():
                frame.hide()
                eff.setOpacity(1.0)

            fo.finished.connect(_hide)
            fo.start(QAbstractAnimation.DeleteWhenStopped)


def _update_all_folder_backgrounds(app: "DemoContent") -> None:
    for btn in app.buttons:
        if btn.is_folder:
            if hasattr(btn, "background_frame") and btn.background_frame:
                btn.background_frame.lower()
    for btn in app.buttons:
        if btn.is_folder:
            _update_folder_background(app, btn)


# ──────────────────────────────────────────────
# DemoContent (the scrollable widget that hosts buttons)
# ──────────────────────────────────────────────

class DemoContent(QWidget):
    """Hosts buttons inside a QScrollArea and manages all
    drag / reorder / merge / folder interactions."""

    def __init__(self, scroll_area: QScrollArea) -> None:
        super().__init__(scroll_area)
        self.scroll_area = scroll_area
        self.scroll_content: DemoContent = self  # self-reference for mixin compat

        self.button_width = BUTTON_WIDTH
        self.button_height = BUTTON_HEIGHT
        self.spacing = SPACING
        self.top_margin = TOP_MARGIN
        self.folder_extra_width = 0

        self.edit_mode: bool = False
        self.buttons: list[DemoButton] = []
        self.proximity_pair: tuple[DemoButton, DemoButton] | None = None
        self.proximity_threshold = PROXIMITY_THRESHOLD

        # Hint frames
        self.frame = HintFrame(self, "border:2px dashed #3498db; background:rgba(52,152,219,.1);")
        self.blue_reorder_frame = HintFrame(self, "border:2px dashed blue; background:rgba(0,0,255,.1);")
        self.red_removal_frame = HintFrame(self, "border:2px dashed red; background:rgba(255,0,0,.1);")
        self.frame_visible: bool = False

        # Folder animation bookkeeping
        self._active_layout_anim: QParallelAnimationGroup | None = None
        self.folder_expanded_states: dict[DemoButton, bool] = {}
        self.all_folders_collapsed: bool = False
        self._expand_retry_timer: QTimer | None = None

    # ─── Button creation ────────────────────────
    def add_button(self, title: str) -> DemoButton:
        btn = DemoButton(title, parent=self)
        btn.app = self
        btn.show()
        self.buttons.append(btn)
        self.update_button_positions()
        return btn

    def delete_button(self, btn: DemoButton) -> None:
        """Delete a button (or folder). Dissolve folders as needed."""
        if btn.is_sub_button and btn.parent_folder:
            self.remove_sub_button_from_folder(btn)
            return
        if btn.is_folder:
            # Remove all sub-buttons
            for sub in list(btn.sub_buttons):
                sub.hide()
                sub.deleteLater()
            btn.sub_buttons.clear()
            if hasattr(btn, "background_frame") and btn.background_frame:
                btn.background_frame.hide()
                btn.background_frame.deleteLater()
        if btn in self.buttons:
            self.buttons.remove(btn)
        btn.hide()
        btn.deleteLater()
        self.update_button_positions()

    # ─── Hint frame helpers (from HintMixin) ────
    def show_frame(self, btn1: DemoButton, btn2: DemoButton) -> None:
        left = min(btn1.x(), btn2.x()) - 10
        top = min(btn1.y(), btn2.y()) - 10
        right = max(btn1.x() + BUTTON_WIDTH, btn2.x() + BUTTON_WIDTH) + 10
        bottom = max(btn1.y() + BUTTON_HEIGHT, btn2.y() + BUTTON_HEIGHT) + 10
        self.frame.setGeometry(left, top, right - left, bottom - top)
        self.frame.show()
        self.frame_visible = True

    def hide_frame(self) -> None:
        self.frame.hide()
        self.frame_visible = False

    def is_button_in_frame(self, button: DemoButton) -> bool:
        if not self.frame_visible:
            return False
        return self.frame.geometry().contains(
            QRect(button.pos(), button.size()).center()
        )

    def show_blue_reorder_frame(self, parent_folder: DemoButton) -> None:
        min_x, min_y, max_x, max_y = _calculate_folder_area(
            parent_folder, parent_folder.sub_buttons
        )
        margin = 10
        if parent_folder.sub_buttons:
            top = min_y - margin
            h = (max_y - min_y) + 2 * margin
        else:
            top = parent_folder.y() + BUTTON_HEIGHT + SPACING - margin
            h = BUTTON_HEIGHT + 2 * margin
        self.blue_reorder_frame.setGeometry(0, top, self.scroll_content.width(), h)
        self.blue_reorder_frame.show()
        self.blue_reorder_frame.raise_()

    def hide_blue_reorder_frame(self) -> None:
        self.blue_reorder_frame.hide()

    def show_red_removal_frame(self, parent_folder: DemoButton) -> None:
        all_btns = [parent_folder] + parent_folder.sub_buttons
        min_x, min_y, max_x, max_y = _calculate_folder_area(parent_folder, all_btns)
        margin = 20
        self.red_removal_frame.setGeometry(
            min_x - margin, min_y - margin,
            (max_x - min_x) + 2 * margin, (max_y - min_y) + 2 * margin,
        )
        self.red_removal_frame.show()
        self.red_removal_frame.raise_()

    def hide_red_removal_frame(self) -> None:
        self.red_removal_frame.hide()

    # ─── Proximity detection (from CoverContent) ──
    def check_button_proximity(self, dragged: DemoButton) -> None:
        if dragged.is_sub_button or dragged.is_folder:
            self.hide_frame()
            self.proximity_pair = None
            return
        closest = None
        min_d = float("inf")
        for t in self.buttons:
            if t is dragged or t.is_sub_button:
                continue
            d = _button_distance(dragged, t)
            if d < min_d:
                min_d = d
                closest = t
        if closest and min_d < self.proximity_threshold:
            self.show_frame(dragged, closest)
            self.proximity_pair = (dragged, closest)
        else:
            self.hide_frame()
            self.proximity_pair = None

    # ─── Layout (from FolderLayoutMixin) ────────
    def update_button_positions(self) -> None:
        avail_w = self.scroll_area.viewport().width()
        dragging = [b for b in self.buttons if getattr(b, "is_dragging", False)]
        for b in self.buttons:
            if b.is_folder:
                for s in b.sub_buttons:
                    if getattr(s, "is_dragging", False):
                        dragging.append(s)
        final_map, _ = self._calculate_final_positions(None, False, dragging)
        for btn, pos in final_map.items():
            if not getattr(btn, "is_dragging", False):
                btn.move(pos)
        max_bottom = 0
        if final_map:
            max_bottom = max(p.y() for p in final_map.values()) + BUTTON_HEIGHT + SPACING
        else:
            max_bottom = SPACING + BUTTON_HEIGHT
        self.setMinimumSize(avail_w, max_bottom)
        _update_all_folder_backgrounds(self)

    def _calculate_final_positions(
        self,
        toggled_folder: DemoButton | None,
        is_expanding: bool,
        skip_buttons: list[DemoButton] | None = None,
    ) -> tuple[dict[DemoButton, QPoint], QPoint]:
        bw, bh, sp = BUTTON_WIDTH, BUTTON_HEIGHT, SPACING
        avail_w = self.scroll_area.viewport().width()
        x, y = sp, sp + TOP_MARGIN
        final: dict[DemoButton, QPoint] = {}
        per_row = max(1, (avail_w + sp) // (bw + sp))
        mi = 0
        skip_set = set(skip_buttons) if skip_buttons else set()

        for btn in self.buttons:
            if btn in skip_set or getattr(btn, "is_dragging", False):
                if mi > 0 and mi % per_row == 0:
                    y += bh + sp
                    x = sp
                final[btn] = btn.pos()
                x += bw + sp
                mi += 1
                continue

            if mi > 0 and mi % per_row == 0:
                y += bh + sp
                x = sp
            final[btn] = QPoint(x, y)

            expanded_final = False
            if btn.is_folder:
                if btn is toggled_folder:
                    expanded_final = is_expanding
                elif btn.is_expanded:
                    expanded_final = True

            if expanded_final:
                y += bh + sp
                sub_x = sp
                fsp = sp * 1.5
                sub_per_row = max(1, int((avail_w + fsp) // (bw + fsp)))
                for idx, sub in enumerate(btn.sub_buttons):
                    if idx > 0 and idx % sub_per_row == 0:
                        y += bh + fsp
                        sub_x = sp
                    if sub in skip_set or getattr(sub, "is_dragging", False):
                        final[sub] = sub.pos()
                        sub_x += bw + fsp
                        continue
                    final[sub] = QPoint(sub_x, int(y))
                    sub_x += bw + fsp
                if btn.sub_buttons:
                    y += bh + sp
                x = sp
                mi = -1
            else:
                x += bw + sp
            mi += 1

        return final, QPoint(0, 0)

    # ─── Reorder (from FolderLayoutMixin) ────────
    def update_button_order(self, dragged: DemoButton) -> None:
        if dragged.is_sub_button:
            return
        main = [b for b in self.buttons if not b.is_sub_button]
        targets = _calculate_main_positions(main, self.scroll_content.width())
        if not targets:
            return
        d_center = dragged.pos() + QPoint(BUTTON_WIDTH // 2, BUTTON_HEIGHT // 2)
        closest_idx = 0
        min_dist = float("inf")
        for i in range(len(main)):
            slot = targets[i] if i < len(targets) else targets[-1]
            sc = slot + QPoint(BUTTON_WIDTH // 2, BUTTON_HEIGHT // 2)
            dist = (d_center - sc).manhattanLength()
            if dist < min_dist:
                min_dist = dist
                closest_idx = i
        if dragged in self.buttons:
            self.buttons.remove(dragged)
        cur_main = [b for b in self.buttons if not b.is_sub_button]
        new_order: list[DemoButton] = []
        it = iter(cur_main)
        for i in range(len(cur_main) + 1):
            if i == closest_idx:
                new_order.append(dragged)
            else:
                try:
                    new_order.append(next(it))
                except StopIteration:
                    break
        self.buttons = new_order
        active = getattr(self, "_active_layout_anim", None)
        if active and active.state() == QParallelAnimationGroup.Running:
            return
        self.animate_button_positions(dragged)

    def animate_button_positions(self, dragged: DemoButton | None = None) -> None:
        main = [b for b in self.buttons if not b.is_sub_button]
        targets = _calculate_main_positions(main, self.scroll_content.width())
        for i, btn in enumerate(main):
            if i < len(targets) and btn is not dragged and not getattr(btn, "is_dragging", False):
                btn.move(targets[i])

    def finalize_button_order(self) -> None:
        main = [b for b in self.buttons if not b.is_sub_button]
        targets = _calculate_main_positions(main, self.scroll_content.width())
        group = QParallelAnimationGroup(self)
        for i, btn in enumerate(main):
            if i < len(targets) and btn.pos() != targets[i]:
                group.addAnimation(_create_pos_anim(btn, targets[i], 300))
        group.finished.connect(self.update_button_positions)
        group.start()

    # ─── Sub-button reorder (from FolderLayoutMixin) ──
    def update_sub_button_order(
        self,
        folder: DemoButton,
        dragged_sub_button: DemoButton | None = None,
        realtime: bool = False,
    ) -> None:
        targets = _calculate_sub_positions(folder, self.scroll_content.width())
        if dragged_sub_button:
            dc = dragged_sub_button.pos() + QPoint(BUTTON_WIDTH // 2, BUTTON_HEIGHT // 2)
            ci = 0
            if targets:
                ci = min(
                    range(len(targets)),
                    key=lambda i: (
                        targets[i] + QPoint(BUTTON_WIDTH // 2, BUTTON_HEIGHT // 2) - dc
                    ).manhattanLength(),
                )
            if dragged_sub_button in folder.sub_buttons:
                folder.sub_buttons.remove(dragged_sub_button)
            new_order = folder.sub_buttons[:]
            new_order.insert(ci, dragged_sub_button)
            folder.sub_buttons = new_order
        if realtime:
            self._finalize_sub_realtime(folder, dragged_sub_button)
        else:
            self._finalize_sub_animated(folder, dragged_sub_button)

    def _finalize_sub_realtime(self, folder: DemoButton, dragged: DemoButton | None) -> None:
        targets = _calculate_sub_positions(folder, self.scroll_content.width())
        for i, btn in enumerate(folder.sub_buttons):
            if i < len(targets) and btn is not dragged and not getattr(btn, "is_dragging", False):
                btn.move(targets[i])
        _update_folder_background(self, folder)

    def _finalize_sub_animated(self, folder: DemoButton, dragged: DemoButton | None) -> None:
        targets = _calculate_sub_positions(folder, self.scroll_content.width())
        group = QParallelAnimationGroup(self)
        for i, btn in enumerate(folder.sub_buttons):
            if (
                i < len(targets)
                and btn is not dragged
                and not getattr(btn, "is_dragging", False)
                and btn.pos() != targets[i]
            ):
                group.addAnimation(_create_pos_anim(btn, targets[i], 300))
        group.finished.connect(lambda: _update_folder_background(self, folder))
        group.start()

    # ─── Folder operations (from FolderOperationMixin) ──
    def merge_folders(self) -> None:
        if not self.proximity_pair:
            return
        btn1, btn2 = self.proximity_pair
        if btn2.is_folder and not btn1.is_folder:
            self._add_to_existing_folder(btn1, btn2)
        elif btn1.is_folder and not btn2.is_folder:
            self._add_to_existing_folder(btn2, btn1)
        elif not btn1.is_folder and not btn2.is_folder:
            self._create_new_folder(btn1, btn2)
        self.update_button_positions()
        self.hide_frame()

    def _add_to_existing_folder(self, src: DemoButton, folder: DemoButton) -> None:
        sub = self._make_sub(src, folder)
        folder.sub_buttons.append(sub)
        if src in self.buttons:
            self.buttons.remove(src)
        src.hide()
        src.deleteLater()
        if self.edit_mode:
            sub.start_jitter()
            if not folder.is_expanded:
                self.toggle_folder(folder)
            else:
                sub.show()
                self.update_button_positions()
        elif folder.is_expanded:
            sub.show()
            self.update_button_positions()

    def _create_new_folder(self, btn1: DemoButton, btn2: DemoButton) -> None:
        name = f"Folder {len([b for b in self.buttons if b.is_folder]) + 1}"
        folder = DemoButton(name, parent=self)
        folder.app = self
        folder.is_folder = True
        folder.is_expanded = False
        folder.sub_buttons = []
        folder.move(btn1.pos())
        sub1 = self._make_sub(btn1, folder)
        sub2 = self._make_sub(btn2, folder)
        folder.sub_buttons.extend([sub1, sub2])
        for old in (btn1, btn2):
            if old in self.buttons:
                self.buttons.remove(old)
            old.hide()
            old.deleteLater()
        self.buttons.append(folder)
        folder.show()
        if self.edit_mode:
            folder.start_jitter()
            sub1.start_jitter()
            sub2.start_jitter()
            self.toggle_folder(folder)
        else:
            self.toggle_folder(folder)

    def _make_sub(self, original: DemoButton, folder: DemoButton) -> DemoButton:
        sub = DemoButton(original.text(), parent=self)
        sub.app = self
        sub.is_sub_button = True
        sub.parent_folder = folder
        sub.hide()
        return sub

    def remove_sub_button_from_folder(self, sub: DemoButton) -> None:
        parent = sub.parent_folder
        if not parent:
            return
        if sub in parent.sub_buttons:
            parent.sub_buttons.remove(sub)
        sub.is_sub_button = False
        sub.parent_folder = None
        if sub not in self.buttons:
            try:
                idx = self.buttons.index(parent)
                self.buttons.insert(idx + 1, sub)
            except ValueError:
                self.buttons.append(sub)
        sub.show()
        if self.edit_mode:
            sub.start_jitter()
        # Dissolve folder if <2 sub-buttons
        self._check_dissolve(parent)
        self.update_button_positions()

    def _check_dissolve(self, folder: DemoButton) -> None:
        if len(folder.sub_buttons) < 2:
            if folder in self.buttons:
                self.buttons.remove(folder)
            for b in folder.sub_buttons:
                b.is_sub_button = False
                b.parent_folder = None
                if b not in self.buttons:
                    self.buttons.append(b)
                b.show()
            if hasattr(folder, "background_frame") and folder.background_frame:
                folder.background_frame.hide()
                folder.background_frame.deleteLater()
            folder.hide()
            folder.deleteLater()

    # ─── Folder toggle animation (from FolderAnimationMixin) ──
    def toggle_folder(self, folder: DemoButton) -> None:
        if not folder.is_folder:
            return
        self._stop_active_layout_anim()
        cur = getattr(folder, "folder_animation_group", None)
        if cur and cur.state() == QParallelAnimationGroup.Running:
            return
        self._stop_folder_animation(folder)

        folder.is_expanded = not folder.is_expanded
        self._ensure_bg(folder)

        if folder.is_expanded:
            for s in folder.sub_buttons:
                s.show()
                s.setWindowOpacity(0)

        final_map, _ = self._calculate_final_positions(folder, folder.is_expanded)
        sub_targets = [final_map[s] for s in folder.sub_buttons if s in final_map]

        toggle_anim = _create_folder_toggle_anim(folder, sub_targets)
        bg_geom, bg_opac = self._build_bg_anims(folder, sub_targets, folder.is_expanded)
        move_others = self._build_move_group(folder, final_map)
        other_bg = self._build_other_bg_group(folder, final_map)

        master = QParallelAnimationGroup()
        master.addAnimation(toggle_anim)
        master.addAnimation(bg_geom)
        master.addAnimation(bg_opac)
        master.addAnimation(move_others)
        master.addAnimation(other_bg)

        folder.folder_animation_group = master
        master.start()

    def _ensure_bg(self, folder: DemoButton) -> None:
        btns = [folder]
        for b in self.buttons:
            if b.is_folder and (b is folder or b.is_expanded):
                if b not in btns:
                    btns.append(b)
        for b in btns:
            if not hasattr(b, "background_frame") or b.background_frame is None:
                b.background_frame = FolderBackground(self.scroll_content)
                eff = QGraphicsOpacityEffect(b.background_frame)
                eff.setOpacity(1.0)
                b.background_frame.setGraphicsEffect(eff)
            b.background_frame.lower()

    def _build_bg_anims(self, folder: DemoButton, sub_targets: list[QPoint],
                        is_expanding: bool) -> tuple[QPropertyAnimation, QPropertyAnimation]:
        frame = folder.background_frame
        eff = frame.graphicsEffect()
        folder_pos = self._calculate_final_positions(folder, is_expanding)[0].get(folder, folder.pos())

        if is_expanding:
            start_g = QRect(folder.pos(), folder.size())
            end_g = _calc_bg_rect(folder_pos, sub_targets)
            frame.setGeometry(start_g)
            frame.show()
            eff.setOpacity(0.0)
        else:
            start_g = frame.geometry()
            end_g = QRect(folder_pos, folder.size())

        ga = QPropertyAnimation(frame, b"geometry")
        ga.setDuration(450)
        ga.setEasingCurve(QEasingCurve.OutBack if is_expanding else QEasingCurve.InBack)
        ga.setStartValue(start_g)
        ga.setEndValue(end_g)

        oa = QPropertyAnimation(eff, b"opacity")
        oa.setDuration(350)
        oa.setEasingCurve(QEasingCurve.InOutQuad)
        if is_expanding:
            oa.setStartValue(0.0)
            oa.setEndValue(1.0)
        else:
            oa.setStartValue(eff.opacity())
            oa.setEndValue(0.0)
            oa.finished.connect(lambda: (frame.hide(), eff.setOpacity(1.0)))
        return ga, oa

    def _build_move_group(self, toggled: DemoButton,
                          final_map: dict[DemoButton, QPoint]) -> QParallelAnimationGroup:
        g = QParallelAnimationGroup()
        for btn, pos in final_map.items():
            if btn in toggled.sub_buttons or btn is toggled or getattr(btn, "is_dragging", False):
                continue
            if btn.pos() != pos:
                g.addAnimation(_create_pos_anim(btn, pos, 450))
        return g

    def _build_other_bg_group(self, toggled: DemoButton,
                              final_map: dict[DemoButton, QPoint]) -> QParallelAnimationGroup:
        g = QParallelAnimationGroup()
        for btn in self.buttons:
            if btn.is_folder and btn.is_expanded and btn is not toggled:
                if hasattr(btn, "background_frame") and btn.background_frame is not None:
                    fp = final_map.get(btn, btn.pos())
                    subs_pos = [final_map[s] for s in btn.sub_buttons if s in final_map]
                    target = _calc_bg_rect(fp, subs_pos)
                    if target != btn.background_frame.geometry() and not target.isEmpty():
                        a = QPropertyAnimation(btn.background_frame, b"geometry")
                        a.setDuration(450)
                        a.setEasingCurve(QEasingCurve.OutBack)
                        a.setStartValue(btn.background_frame.geometry())
                        a.setEndValue(target)
                        g.addAnimation(a)
        return g

    def _post_folder_animation(self, folder: DemoButton) -> None:
        if not folder.is_expanded:
            for s in folder.sub_buttons:
                s.hide()
                s.setWindowOpacity(1.0)
            if hasattr(folder, "background_frame") and folder.background_frame:
                folder.background_frame.hide()
                if folder.background_frame.graphicsEffect():
                    folder.background_frame.graphicsEffect().setOpacity(1.0)
        else:
            for s in folder.sub_buttons:
                s.setWindowOpacity(1.0)
            if hasattr(folder, "background_frame") and folder.background_frame:
                if folder.background_frame.graphicsEffect():
                    folder.background_frame.graphicsEffect().setOpacity(1.0)
        self.update_button_positions()

    # ─── Collapse / expand all (from FolderAnimationMixin) ──
    def _stop_active_layout_anim(self) -> None:
        anim = getattr(self, "_active_layout_anim", None)
        if not anim:
            return
        if anim.state() != QParallelAnimationGroup.Stopped:
            anim.stop()
        finalized = False
        for btn in self.buttons:
            ba = getattr(btn, "folder_animation_group", None)
            if not ba:
                continue
            if ba is anim or ba.parent() is anim:
                self._stop_folder_animation(btn, finalize=True)
                finalized = True
        if finalized:
            self.update_button_positions()
        anim.deleteLater()
        self._active_layout_anim = None
        self._ensure_final_layout()

    def _stop_folder_animation(self, folder: DemoButton, *, finalize: bool = False) -> None:
        anim = getattr(folder, "folder_animation_group", None)
        if anim and anim.state() != QParallelAnimationGroup.Stopped:
            anim.stop()
        if anim:
            anim.deleteLater()
        folder.folder_animation_group = None
        timer = getattr(folder, "follow_timer", None)
        if timer:
            if timer.isActive():
                timer.stop()
            timer.deleteLater()
        folder.follow_timer = None
        if finalize:
            try:
                self._post_folder_animation(folder)
            except Exception:
                pass

    def _ensure_final_layout(self) -> None:
        dragging: list[DemoButton] = []
        for b in self.buttons:
            if getattr(b, "is_dragging", False):
                dragging.append(b)
            if b.is_folder:
                for s in b.sub_buttons:
                    if getattr(s, "is_dragging", False):
                        dragging.append(s)
        fm, _ = self._calculate_final_positions(None, False, dragging)
        for b, p in fm.items():
            if b.pos() != p:
                self.update_button_positions()
                return

    def collapse_all_folders(self, *, skip_buttons: list[DemoButton] | None = None) -> None:
        self._stop_active_layout_anim()
        if hasattr(self, "_expand_retry_timer") and self._expand_retry_timer:
            if self._expand_retry_timer.isActive():
                self._expand_retry_timer.stop()
            self._expand_retry_timer.deleteLater()
            self._expand_retry_timer = None

        for btn in self.buttons:
            if btn.is_folder:
                finalize = not btn.is_expanded
                self._stop_folder_animation(btn, finalize=finalize)

        self.folder_expanded_states = {}
        self.all_folders_collapsed = True

        to_collapse = [b for b in self.buttons if b.is_folder and b.is_expanded]
        if not to_collapse:
            QTimer.singleShot(0, self.update_button_positions)
            return

        for f in to_collapse:
            self.folder_expanded_states[f] = True
            self._stop_folder_animation(f)
            f.is_expanded = False

        final_map, _ = self._calculate_final_positions(None, False, skip_buttons)
        master = QParallelAnimationGroup(self)
        for f in to_collapse:
            a = _create_folder_toggle_anim(
                f, [], connect_finished=False,
                follow_parent=f in (skip_buttons or []),
            )
            f.folder_animation_group = a
            master.addAnimation(a)
        for btn, pos in final_map.items():
            if btn.pos() != pos:
                master.addAnimation(_create_pos_anim(btn, pos, 450))

        def _cleanup() -> None:
            for f in to_collapse:
                if not f.is_expanded:
                    for s in f.sub_buttons:
                        s.hide()
                        s.setWindowOpacity(1.0)
                    if hasattr(f, "background_frame") and f.background_frame:
                        f.background_frame.hide()
                        if f.background_frame.graphicsEffect():
                            f.background_frame.graphicsEffect().setOpacity(1.0)
                else:
                    for s in f.sub_buttons:
                        s.setWindowOpacity(1.0)
                    if hasattr(f, "background_frame") and f.background_frame and f.background_frame.graphicsEffect():
                        f.background_frame.graphicsEffect().setOpacity(1.0)
                f.folder_animation_group = None
            self.update_button_positions()
            self._ensure_final_layout()
            self._active_layout_anim = None

        master.finished.connect(_cleanup)
        self._active_layout_anim = master
        master.start()

    def expand_all_folders(self) -> None:
        self._stop_active_layout_anim()
        if (
            not getattr(self, "all_folders_collapsed", False)
            or not self.folder_expanded_states
        ):
            QTimer.singleShot(0, self.update_button_positions)
            return

        expand_targets = [
            b for b, was in self.folder_expanded_states.items()
            if was and b in self.buttons and not b.is_expanded
        ]
        for btn in self.buttons:
            if btn.is_folder:
                finalize = btn not in expand_targets
                self._stop_folder_animation(btn, finalize=finalize)

        if not expand_targets:
            self.folder_expanded_states.clear()
            self.all_folders_collapsed = False
            QTimer.singleShot(0, self.update_button_positions)
            return

        needs_retry = any(
            getattr(b, "folder_animation_group", None)
            and b.folder_animation_group.state() == QParallelAnimationGroup.Running
            for b in expand_targets
        )
        if needs_retry:
            if not self._expand_retry_timer:
                self._expand_retry_timer = QTimer(self, singleShot=True)
                self._expand_retry_timer.timeout.connect(self.expand_all_folders)
            self._expand_retry_timer.start(150)
            return
        if self._expand_retry_timer:
            if self._expand_retry_timer.isActive():
                self._expand_retry_timer.stop()
            self._expand_retry_timer.deleteLater()
            self._expand_retry_timer = None

        for b in expand_targets:
            self._stop_folder_animation(b)
            b.is_expanded = True
            for s in b.sub_buttons:
                s.show()
                s.setWindowOpacity(0)

        final_map, _ = self._calculate_final_positions(None, False)
        master = QParallelAnimationGroup(self)
        for b in expand_targets:
            st = [final_map[s] for s in b.sub_buttons if s in final_map]
            a = _create_folder_toggle_anim(b, st, connect_finished=False)
            b.folder_animation_group = a
            master.addAnimation(a)

        expanded_subs = [s for f in expand_targets for s in f.sub_buttons]
        for b, pos in final_map.items():
            if b in expanded_subs:
                continue
            if b.pos() != pos:
                master.addAnimation(_create_pos_anim(b, pos, 450))

        def _cleanup() -> None:
            for f in expand_targets:
                if not f.is_expanded:
                    for s in f.sub_buttons:
                        s.hide()
                        s.setWindowOpacity(1.0)
                    if hasattr(f, "background_frame") and f.background_frame:
                        f.background_frame.hide()
                        if f.background_frame.graphicsEffect():
                            f.background_frame.graphicsEffect().setOpacity(1.0)
                else:
                    for s in f.sub_buttons:
                        s.setWindowOpacity(1.0)
                    if hasattr(f, "background_frame") and f.background_frame and f.background_frame.graphicsEffect():
                        f.background_frame.graphicsEffect().setOpacity(1.0)
                f.folder_animation_group = None
            self.folder_expanded_states.clear()
            self.all_folders_collapsed = False
            self.update_button_positions()
            self._ensure_final_layout()
            self._active_layout_anim = None

        master.finished.connect(_cleanup)
        self._active_layout_anim = master
        master.start()

    # ─── Edit mode toggle ───────────────────────
    def set_edit_mode(self, on: bool) -> None:
        self.edit_mode = on
        for btn in self.buttons:
            if on:
                btn.start_jitter()
            else:
                btn.stop_jitter()
            if btn.is_folder:
                for sub in btn.sub_buttons:
                    if on:
                        sub.start_jitter()
                    else:
                        sub.stop_jitter()

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self.update_button_positions()


# ──────────────────────────────────────────────
# Main window
# ──────────────────────────────────────────────

class DemoWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("WordBook Button Demo – drag / merge / split")
        self.resize(600, 500)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.setCentralWidget(self.scroll_area)

        self.content = DemoContent(self.scroll_area)
        self.scroll_area.setWidget(self.content)

        # Create sample buttons
        for i in range(1, 9):
            self.content.add_button(f"Book {i}")

        # Toggle edit-mode button
        self._edit_btn = QPushButton("Enter Edit Mode", self)
        self._edit_btn.setFixedHeight(32)
        self._edit_btn.move(10, 4)
        self._edit_btn.clicked.connect(self._toggle_edit)
        self._edit_btn.raise_()

        # Add button
        self._add_btn = QPushButton("+ Add", self)
        self._add_btn.setFixedHeight(32)
        self._add_btn.move(160, 4)
        self._add_btn.clicked.connect(self._add_book)
        self._add_btn.raise_()

        self._counter = 9

    def _toggle_edit(self) -> None:
        on = not self.content.edit_mode
        self.content.set_edit_mode(on)
        self._edit_btn.setText("Exit Edit Mode" if on else "Enter Edit Mode")

    def _add_book(self) -> None:
        self.content.add_button(f"Book {self._counter}")
        self._counter += 1

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._edit_btn.move(10, 4)
        self._add_btn.move(10 + self._edit_btn.width() + 10, 4)


# ──────────────────────────────────────────────
# Entry-point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = DemoWindow()
    win.show()
    sys.exit(app.exec())
