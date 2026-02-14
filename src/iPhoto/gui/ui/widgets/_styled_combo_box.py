"""Styled combo box matching the edit sidebar theme."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QWidget


class _StyledComboBox(QComboBox):
    """Styled combo box matching the edit sidebar theme."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("""
            QComboBox {
                background-color: #383838;
                color: white;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 13px;
                border: 1px solid #555;
            }
            QComboBox::drop-down {
                border: 0px;
                width: 25px;
            }
            QComboBox::down-arrow {
                image: none;
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #383838;
                color: white;
                selection-background-color: #505050;
                border: 1px solid #555;
            }
        """)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        arrow_color = QColor("#4a90e2")
        rect = self.rect()
        cx = rect.width() - 15
        cy = rect.height() / 2
        size = 4
        p1 = QPointF(cx - size, cy - size / 2)
        p2 = QPointF(cx, cy + size / 2)
        p3 = QPointF(cx + size, cy - size / 2)
        pen = QPen(arrow_color, 2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(p1, p2)
        painter.drawLine(p2, p3)
        painter.end()
