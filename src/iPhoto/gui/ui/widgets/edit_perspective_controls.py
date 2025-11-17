from __future__ import annotations
"""Perspective correction controls for the crop sidebar page."""
"""Widgets that implement the crop sidebar perspective slider group."""



from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..icon import load_icon
from ..models.edit_session import EditSession
from .edit_strip import BWSlider


_PERSPECTIVE_VERTICAL_KEY = "Perspective_Vertical"
_PERSPECTIVE_HORIZONTAL_KEY = "Perspective_Horizontal"


class _PerspectiveSliderRow(QWidget):
    """Single icon + slider row used by the perspective control group."""

    valueChanged = Signal(float)
    valueCommitted = Signal(float)

    def __init__(self, label: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slider = BWSlider(label, self, minimum=-1.0, maximum=1.0, initial=0.0)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        icon_label = QLabel(self)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = load_icon(icon_name)
        icon_label.setPixmap(icon.pixmap(24, 24))
        icon_label.setFixedSize(28, 28)
        layout.addWidget(icon_label)

        layout.addWidget(self._slider, 1)

        self._slider.valueChanged.connect(self.valueChanged)
        self._slider.valueCommitted.connect(self.valueCommitted)

    def set_value(self, value: float, *, emit: bool = False) -> None:
        """Update the slider without re-broadcasting the signal by default."""

        self._slider.setValue(value, emit=emit)

    def value(self) -> float:
        return self._slider.value()


class PerspectiveControls(QWidget):
    """Fixed control group that exposes vertical and horizontal sliders."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: Optional[EditSession] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        header = QLabel("Perspective", self)
        header.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStyleSheet("font-weight: 600; color: palette(window-text);")
        layout.addWidget(header)

        self._vertical_row = _PerspectiveSliderRow(
            "Vertical",
            "perspective.vertical.svg",
            self,
        )
        self._horizontal_row = _PerspectiveSliderRow(
            "Horizontal",
            "perspective.horizontal.svg",
            self,
        )

        layout.addWidget(self._vertical_row)
        layout.addWidget(self._horizontal_row)
        layout.addStretch(1)

        self._vertical_row.valueChanged.connect(self._on_vertical_value_changed)
        self._horizontal_row.valueChanged.connect(self._on_horizontal_value_changed)

    # ------------------------------------------------------------------
    def bind_session(self, session: Optional[EditSession]) -> None:
        """Attach the sliders to *session* so they stay in sync with edits."""

        if self._session is session:
            return
        if self._session is not None:
            try:
                self._session.valueChanged.disconnect(self._on_session_value_changed)
            except (TypeError, RuntimeError):
                pass
        self._session = session
        if session is not None:
            session.valueChanged.connect(self._on_session_value_changed)
            self._sync_from_session()
        else:
            self._vertical_row.set_value(0.0)
            self._horizontal_row.set_value(0.0)

    def refresh_from_session(self) -> None:
        """Force the sliders to reload their values from the session."""

        if self._session is None:
            self._vertical_row.set_value(0.0)
            self._horizontal_row.set_value(0.0)
            return
        self._sync_from_session()

    # ------------------------------------------------------------------
    def _on_vertical_value_changed(self, value: float) -> None:
        self._update_session_value(_PERSPECTIVE_VERTICAL_KEY, value)

    def _on_horizontal_value_changed(self, value: float) -> None:
        self._update_session_value(_PERSPECTIVE_HORIZONTAL_KEY, value)

    def _update_session_value(self, key: str, value: float) -> None:
        if self._session is None:
            return
        self._session.set_value(key, value)

    def _on_session_value_changed(self, key: str, value: object) -> None:
        if key == _PERSPECTIVE_VERTICAL_KEY:
            self._vertical_row.set_value(float(value))
        elif key == _PERSPECTIVE_HORIZONTAL_KEY:
            self._horizontal_row.set_value(float(value))

    def _sync_from_session(self) -> None:
        if self._session is None:
            return
        self._vertical_row.set_value(float(self._session.value(_PERSPECTIVE_VERTICAL_KEY)))
        self._horizontal_row.set_value(float(self._session.value(_PERSPECTIVE_HORIZONTAL_KEY)))
