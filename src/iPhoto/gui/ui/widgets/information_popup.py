"""Custom frameless popup window for displaying informational messages."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..icons import load_icon
from .main_window_metrics import WINDOW_CONTROL_BUTTON_SIZE, WINDOW_CONTROL_GLYPH_SIZE


class InformationPopup(QWidget):
    """Frameless rounded popup that presents an informational message.

    The window reuses the same close-button styling as the main application
    window (``red.close.circle.svg`` glyph rendered at
    :data:`WINDOW_CONTROL_GLYPH_SIZE` inside a :data:`WINDOW_CONTROL_BUTTON_SIZE`
    hit target) so the user experience remains consistent.

    Usage::

        popup = InformationPopup(title="Notice", message="Operation complete.")
        popup.show()
    """

    _DEFAULT_WIDTH = 360
    _CORNER_RADIUS = 12.0

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "Information",
        message: str = "",
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumWidth(self._DEFAULT_WIDTH)

        self._drag_active = False
        self._drag_offset = None

        # -- root layout ---------------------------------------------------
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # -- title bar -----------------------------------------------------
        self._title_bar = QWidget(self)
        self._title_bar.setFixedHeight(
            WINDOW_CONTROL_BUTTON_SIZE.height() + 16,
        )
        title_layout = QHBoxLayout(self._title_bar)
        title_layout.setContentsMargins(16, 10, 12, 6)
        title_layout.setSpacing(8)

        self._title_label = QLabel(title, self._title_bar)
        self._title_label.setObjectName("popupTitleLabel")
        self._title_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        )
        self._title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        title_layout.addWidget(self._title_label, 1)

        # Close button – reuses the main window's close-button appearance.
        self._close_button = QToolButton(self._title_bar)
        self._close_button.setIcon(load_icon("red.close.circle.svg"))
        self._close_button.setIconSize(WINDOW_CONTROL_GLYPH_SIZE)
        self._close_button.setFixedSize(WINDOW_CONTROL_BUTTON_SIZE)
        self._close_button.setAutoRaise(True)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._close_button.setToolTip("Close")
        self._close_button.clicked.connect(self.close)
        title_layout.addWidget(
            self._close_button, 0, Qt.AlignmentFlag.AlignRight,
        )

        root_layout.addWidget(self._title_bar)

        # -- content area --------------------------------------------------
        self._message_label = QLabel(message, self)
        self._message_label.setObjectName("popupMessageLabel")
        self._message_label.setWordWrap(True)
        self._message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse,
        )
        self._message_label.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        self._message_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._message_label.setContentsMargins(16, 8, 16, 16)
        root_layout.addWidget(self._message_label, 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def close_button(self) -> QToolButton:
        """Expose the close button for external signal wiring."""

        return self._close_button

    def set_title(self, title: str) -> None:
        """Update the popup title text."""

        self._title_label.setText(title)

    def title(self) -> str:
        """Return the current popup title."""

        return self._title_label.text()

    def set_message(self, message: str) -> None:
        """Replace the information message displayed in the body."""

        self._message_label.setText(message)

    def message(self) -> str:
        """Return the current information message."""

        return self._message_label.text()

    # ------------------------------------------------------------------
    # QWidget overrides
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # type: ignore[override]
        """Draw an anti-aliased rounded rectangle matching the window palette."""

        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(
            self._CORNER_RADIUS,
            min(rect.width(), rect.height()) / 2.0,
        )

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        bg_color = self.palette().color(QPalette.ColorRole.Window)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, bg_color)

        border_color = self.palette().color(QPalette.ColorRole.Mid)
        border_color.setAlpha(80)
        painter.setPen(border_color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """Begin a drag when clicking on the title bar area."""

        if event.button() == Qt.MouseButton.LeftButton:
            local_pos = event.position().toPoint()
            if self._title_bar.geometry().contains(local_pos):
                self._drag_active = True
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """Move the popup when dragging the title bar."""

        if self._drag_active and self._drag_offset is not None:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_pos)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """End a title-bar drag."""

        if self._drag_active:
            self._drag_active = False
            self._drag_offset = None
            return
        super().mouseReleaseEvent(event)


__all__ = ["InformationPopup"]
