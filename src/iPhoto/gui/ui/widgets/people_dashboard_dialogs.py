"""Dialog widgets used by the People dashboard."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from iPhoto.people.repository import PersonSummary

from .flow_layout import FlowLayout
from .people_dashboard_shared import (
    AVATAR_SIZE,
    AVATAR_TILE_HEIGHT,
    AVATAR_TILE_WIDTH,
    PLACEHOLDER_BACKDROPS,
    _pixmap_from_image_path,
    _qcolor,
    _widget_uses_dark_theme,
)


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
        self._panel.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.94);
                border: 1px solid rgba(255, 255, 255, 0.65);
                border-radius: 28px;
            }
            """)
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
        merge_button.setStyleSheet("""
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
            """)

        cancel_button = QPushButton("Cancel")
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_button.setFixedHeight(40)
        cancel_button.setStyleSheet("""
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
            """)

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


class GroupAvatarTile(QWidget):
    clicked = Signal(int, bool)

    def __init__(
        self,
        summary: PersonSummary,
        index: int,
        *,
        dark_mode: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.summary = summary
        self.index = index
        self._dark_mode = dark_mode
        self._selected = False
        self._avatar: QPixmap | None = None
        self.setFixedSize(AVATAR_TILE_WIDTH, AVATAR_TILE_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def person_id(self) -> str:
        return self.summary.person_id

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self.update()

    def _avatar_pixmap(self) -> QPixmap | None:
        if self._avatar is None and self.summary.thumbnail_path is not None:
            self._avatar = _pixmap_from_image_path(
                self.summary.thumbnail_path,
                (AVATAR_SIZE * 2, AVATAR_SIZE * 2),
            )
        return self._avatar

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        avatar_left = (self.width() - AVATAR_SIZE) / 2
        avatar_rect = QRectF(avatar_left, 4, AVATAR_SIZE, AVATAR_SIZE)
        avatar_path = QPainterPath()
        avatar_path.addEllipse(avatar_rect)

        painter.save()
        painter.setClipPath(avatar_path)
        pixmap = self._avatar_pixmap()
        if pixmap is not None:
            painter.drawPixmap(avatar_rect.toRect(), pixmap)
        else:
            top, bottom = PLACEHOLDER_BACKDROPS[self.index % len(PLACEHOLDER_BACKDROPS)]
            gradient = QLinearGradient(avatar_rect.topLeft(), avatar_rect.bottomRight())
            gradient.setColorAt(0.0, _qcolor(top))
            gradient.setColorAt(1.0, _qcolor(bottom))
            painter.fillPath(avatar_path, gradient)
        painter.restore()

        border_color = QColor("#0A84FF")
        if not self._selected:
            border_color = QColor(255, 255, 255, 42) if self._dark_mode else QColor(17, 24, 39, 34)
        painter.setPen(QPen(border_color, 4 if self._selected else 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(avatar_rect.adjusted(2, 2, -2, -2))

        name = self.summary.name or ""
        if name:
            painter.setPen(QColor("#E8ECF8") if self._dark_mode else QColor("#1F2937"))
            painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            label_rect = QRectF(4, AVATAR_SIZE + 12, self.width() - 8, 36)
            painter.drawText(
                label_rect,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                name,
            )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            shift_pressed = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.clicked.emit(self.index, shift_pressed)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class GroupPeopleDialog(QDialog):
    def __init__(
        self,
        summaries: list[PersonSummary],
        *,
        initial_selected_ids: list[str] | tuple[str, ...] = (),
        dark_mode: bool | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent.window() if parent is not None else None)
        self._summaries = list(summaries)
        self._tiles: list[GroupAvatarTile] = []
        self._selected_ids: set[str] = set()
        self._selection_order: list[str] = []
        self._anchor_index: int | None = None
        self._dark_mode = _widget_uses_dark_theme(parent) if dark_mode is None else bool(dark_mode)

        self.setModal(True)
        self.setWindowTitle("People")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(920, 640)
        self.setMinimumSize(760, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(34, 34, 34, 34)
        root.setSpacing(0)

        self._panel = QFrame(self)
        self._panel.setObjectName("GroupPeopleDialogPanel")
        panel_bg = "#171B27" if self._dark_mode else "#FFFFFF"
        panel_border = "rgba(255, 255, 255, 0.08)" if self._dark_mode else "#E5E7EB"
        text_primary = "#F6F7FB" if self._dark_mode else "#111827"
        text_secondary = "#DDE3F3" if self._dark_mode else "#374151"
        scroll_handle = "rgba(255, 255, 255, 0.42)" if self._dark_mode else "#CBD5E1"
        cancel_bg = "rgba(255, 255, 255, 0.08)" if self._dark_mode else "#F3F4F6"
        cancel_hover = "rgba(255, 255, 255, 0.13)" if self._dark_mode else "#E5E7EB"
        cancel_text = "#F4F6FB" if self._dark_mode else "#111827"
        disabled_text = "rgba(244, 246, 251, 0.34)" if self._dark_mode else "#9CA3AF"

        self._panel.setStyleSheet(f"""
            #GroupPeopleDialogPanel {{
                background: {panel_bg};
                border: 1px solid {panel_border};
                border-radius: 24px;
            }}
            QLabel {{
                background: transparent;
            }}
            """)
        shadow = QGraphicsDropShadowEffect(self._panel)
        shadow.setBlurRadius(76 if self._dark_mode else 68)
        shadow.setOffset(0, 14 if self._dark_mode else 12)
        shadow.setColor(QColor(0, 0, 0, 56 if self._dark_mode else 48))
        self._panel.setGraphicsEffect(shadow)
        root.addWidget(self._panel)

        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(20, 12, 20, 16)
        panel_layout.setSpacing(14)

        title = QLabel("People")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {text_primary}; font-size: 14px; font-weight: 800;")
        panel_layout.addWidget(title)

        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 9px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {scroll_handle};
                border-radius: 4px;
                min-height: 48px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            """)
        self._tile_host = QWidget()
        self._tile_host.setStyleSheet("background: transparent;")
        self._tile_layout = FlowLayout(self._tile_host, margin=6, h_spacing=40, v_spacing=20)
        self._scroll.setWidget(self._tile_host)
        panel_layout.addWidget(self._scroll, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(12)
        footer.addStretch(1)

        prompt = QLabel("Select People")
        prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt.setStyleSheet(f"color: {text_secondary}; font-size: 13px; font-weight: 700;")
        footer.addWidget(prompt)
        footer.addStretch(1)

        self.cancel_button = QPushButton("Cancel")
        self.add_button = QPushButton("Add")
        for button in (self.cancel_button, self.add_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(38)
            button.setMinimumWidth(86)
        self.cancel_button.setStyleSheet(f"""
            QPushButton {{
                background: {cancel_bg};
                color: {cancel_text};
                border: none;
                border-radius: 10px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton:hover {{ background: {cancel_hover}; }}
            """)
        self.add_button.setStyleSheet(f"""
            QPushButton {{
                background: #0A84FF;
                color: white;
                border: none;
                border-radius: 10px;
                font-size: 13px;
                font-weight: 800;
            }}
            QPushButton:hover:enabled {{ background: #2A95FF; }}
            QPushButton:disabled {{
                background: {cancel_bg};
                color: {disabled_text};
            }}
            """)
        self.cancel_button.clicked.connect(self.reject)
        self.add_button.clicked.connect(self.accept)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.add_button)
        panel_layout.addLayout(footer)

        for index, summary in enumerate(self._summaries):
            tile = GroupAvatarTile(
                summary,
                index,
                dark_mode=self._dark_mode,
                parent=self._tile_host,
            )
            tile.clicked.connect(self._handle_tile_clicked)
            self._tile_layout.addWidget(tile)
            self._tiles.append(tile)

        initial_ids = [person_id for person_id in initial_selected_ids if person_id]
        for person_id in initial_ids:
            self._select_person_id(person_id)
        if initial_ids:
            self._anchor_index = next(
                (
                    index
                    for index, summary in enumerate(self._summaries)
                    if summary.person_id == initial_ids[-1]
                ),
                None,
            )
        self._sync_tiles()

    def selected_person_ids(self) -> list[str]:
        return list(self._selection_order)

    def _handle_tile_clicked(self, index: int, shift_pressed: bool) -> None:
        if not (0 <= index < len(self._summaries)):
            return
        if shift_pressed and self._anchor_index is not None:
            start, end = sorted((self._anchor_index, index))
            for range_index in range(start, end + 1):
                self._select_person_id(self._summaries[range_index].person_id)
        else:
            person_id = self._summaries[index].person_id
            if person_id in self._selected_ids:
                self._selected_ids.remove(person_id)
                self._selection_order = [
                    selected_id for selected_id in self._selection_order if selected_id != person_id
                ]
            else:
                self._select_person_id(person_id)
        self._anchor_index = index
        self._sync_tiles()

    def _select_person_id(self, person_id: str) -> None:
        if person_id in self._selected_ids:
            return
        self._selected_ids.add(person_id)
        self._selection_order.append(person_id)

    def _sync_tiles(self) -> None:
        for tile in self._tiles:
            tile.set_selected(tile.person_id in self._selected_ids)
        self.add_button.setEnabled(len(self._selected_ids) >= 2)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        overlay = QColor(13, 15, 22, 80) if self._dark_mode else QColor(243, 246, 252, 92)
        painter.fillRect(self.rect(), overlay)
