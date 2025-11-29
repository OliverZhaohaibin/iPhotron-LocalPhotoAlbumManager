from __future__ import annotations
"""Perspective correction controls for the crop sidebar page."""
"""Widgets that implement the crop sidebar perspective slider group."""



from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..icon import load_icon
from ..models.edit_session import EditSession
from .edit_strip import BWSlider


_PERSPECTIVE_VERTICAL_KEY = "Perspective_Vertical"
_PERSPECTIVE_HORIZONTAL_KEY = "Perspective_Horizontal"
_STRAIGHTEN_KEY = "Crop_Straighten"
_FLIP_KEY = "Crop_FlipH"


class _PerspectiveSliderRow(QWidget):
    """Single icon + slider row used by the perspective control group."""

    valueChanged = Signal(float)
    valueCommitted = Signal(float)
    interactionStarted = Signal()
    interactionFinished = Signal()

    def __init__(
        self,
        label: str,
        icon_name: str,
        parent: QWidget | None = None,
        *,
        minimum: float = -1.0,
        maximum: float = 1.0,
    ) -> None:
        super().__init__(parent)
        self._slider = BWSlider(label, self, minimum=minimum, maximum=maximum, initial=0.0)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        icon_label = QLabel(self)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = load_icon(icon_name)
        icon_label.setPixmap(icon.pixmap(28, 28))
        icon_label.setFixedSize(32, 32)
        layout.addWidget(icon_label)

        layout.addWidget(self._slider, 1)

        self._slider.valueChanged.connect(self.valueChanged)
        self._slider.valueCommitted.connect(self.valueCommitted)
        self._slider.interactionStarted.connect(self.interactionStarted)
        self._slider.interactionFinished.connect(self.interactionFinished)

    def set_value(self, value: float, *, emit: bool = False) -> None:
        """Update the slider without re-broadcasting the signal by default."""

        self._slider.setValue(value, emit=emit)

    def value(self) -> float:
        return self._slider.value()


class PerspectiveControls(QWidget):
    """Fixed control group that exposes vertical and horizontal sliders."""

    interactionStarted = Signal()
    """Relayed when either slider begins a user interaction."""

    interactionFinished = Signal()
    """Emitted once the current slider interaction concludes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: Optional[EditSession] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self._straighten_row = _PerspectiveSliderRow(
            "Straighten",
            "rotate.circle.horizontal.svg",
            self,
            minimum=-45.0,
            maximum=45.0,
        )
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

        layout.addWidget(self._straighten_row)
        layout.addWidget(self._vertical_row)
        layout.addWidget(self._horizontal_row)
        self._flip_row = _FlipToggleRow("Flip", "flip.horizontal.fill.svg", self)
        layout.addWidget(self._flip_row)
        layout.addStretch(1)

        self._straighten_row.valueChanged.connect(self._on_straighten_changed)
        self._vertical_row.valueChanged.connect(self._on_vertical_value_changed)
        self._horizontal_row.valueChanged.connect(self._on_horizontal_value_changed)
        self._flip_row.toggled.connect(self._on_flip_toggled)
        self._straighten_row.interactionStarted.connect(self.interactionStarted)
        self._vertical_row.interactionStarted.connect(self.interactionStarted)
        self._horizontal_row.interactionStarted.connect(self.interactionStarted)
        self._flip_row.interactionStarted.connect(self.interactionStarted)
        self._straighten_row.interactionFinished.connect(self.interactionFinished)
        self._vertical_row.interactionFinished.connect(self.interactionFinished)
        self._horizontal_row.interactionFinished.connect(self.interactionFinished)
        self._flip_row.interactionFinished.connect(self.interactionFinished)

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
            try:
                self._session.valuesChanged.disconnect(self._on_session_values_changed)
            except (TypeError, RuntimeError):
                pass
        self._session = session
        if session is not None:
            session.valueChanged.connect(self._on_session_value_changed)
            # Listen for batched updates (for example, a 90° rotation that remaps
            # the perspective axes) so the sliders mirror the latest geometry even
            # when individual valueChanged signals are intentionally suppressed.
            session.valuesChanged.connect(self._on_session_values_changed)
            self._sync_from_session()
        else:
            self._straighten_row.set_value(0.0)
            self._vertical_row.set_value(0.0)
            self._horizontal_row.set_value(0.0)
            self._flip_row.set_checked(False)

    def refresh_from_session(self) -> None:
        """Force the sliders to reload their values from the session."""

        if self._session is None:
            self._straighten_row.set_value(0.0)
            self._vertical_row.set_value(0.0)
            self._horizontal_row.set_value(0.0)
            self._flip_row.set_checked(False)
            return
        self._sync_from_session()

    # ------------------------------------------------------------------
    def _on_vertical_value_changed(self, value: float) -> None:
        self._update_session_value(_PERSPECTIVE_VERTICAL_KEY, value)

    def _on_horizontal_value_changed(self, value: float) -> None:
        self._update_session_value(_PERSPECTIVE_HORIZONTAL_KEY, value)

    def _on_straighten_changed(self, value: float) -> None:
        self._update_session_value(_STRAIGHTEN_KEY, value)

    def _on_flip_toggled(self, enabled: bool) -> None:
        if self._session is None:
            return
        self._session.set_value(_FLIP_KEY, enabled)

    def _update_session_value(self, key: str, value: float) -> None:
        if self._session is None:
            return
        self._session.set_value(key, value)

    def _on_session_value_changed(self, key: str, value: object) -> None:
        if key == _PERSPECTIVE_VERTICAL_KEY:
            self._vertical_row.set_value(float(value))
        elif key == _PERSPECTIVE_HORIZONTAL_KEY:
            self._horizontal_row.set_value(float(value))
        elif key == _STRAIGHTEN_KEY:
            self._straighten_row.set_value(float(value))
        elif key == _FLIP_KEY:
            self._flip_row.set_checked(bool(value))

    def _on_session_values_changed(self, _values: dict) -> None:
        """Refresh every control after a batch update such as a rotation."""

        # ``valuesChanged`` delivers the full mapping, so simply reload from the
        # authoritative session state without attempting to diff the payload.  The
        # slider helpers avoid emitting signals when the value is unchanged,
        # preventing feedback loops during continuous drags.
        self._sync_from_session()

    def _sync_from_session(self) -> None:
        if self._session is None:
            return
        self._straighten_row.set_value(float(self._session.value(_STRAIGHTEN_KEY)))
        self._vertical_row.set_value(float(self._session.value(_PERSPECTIVE_VERTICAL_KEY)))
        self._horizontal_row.set_value(float(self._session.value(_PERSPECTIVE_HORIZONTAL_KEY)))
        self._flip_row.set_checked(bool(self._session.value(_FLIP_KEY)))


class _FlipToggleRow(QWidget):
    """Icon + label row that toggles horizontal flipping."""

    toggled = Signal(bool)
    interactionStarted = Signal()
    interactionFinished = Signal()

    def __init__(self, label: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 0, 0, 0)
        layout.setSpacing(0)
        self._button = QToolButton(self)
        self._button.setAutoRaise(True)
        self._button.setCheckable(True)
        self._button.setIcon(load_icon(icon_name, color=(180, 180, 180)))
        self._button.setIconSize(QSize(22, 22))
        self._button.clicked.connect(self._handle_clicked)
        layout.addWidget(self._button)

        self._label_button = QPushButton(label, self)
        self._label_button.setFlat(True)
        self._label_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._label_button.clicked.connect(self._toggle)
        layout.addWidget(self._label_button, 1)

    def set_checked(self, checked: bool) -> None:
        if self._button.isChecked() == checked:
            return
        self._button.setChecked(checked)
        self._label_button.setDown(checked)

    def _toggle(self) -> None:
        # Toggle button state and emit signal to stay consistent with icon button
        new_state = not self._button.isChecked()
        self._button.setChecked(new_state)
        self.interactionStarted.emit()
        self._label_button.setDown(new_state)
        self.toggled.emit(new_state)
        self.interactionFinished.emit()

    def _handle_clicked(self, checked: bool) -> None:
        self.interactionStarted.emit()
        self._label_button.setDown(checked)
        self.toggled.emit(checked)
        self.interactionFinished.emit()

