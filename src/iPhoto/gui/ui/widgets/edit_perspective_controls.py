from __future__ import annotations
"""Perspective correction controls for the crop sidebar page."""



from typing import Optional

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..icon import load_icon
from ..icon import icon_path as _icon_path
from ..models.edit_session import EditSession
from ..palette import Edit_SIDEBAR_FONT
from .edit_strip import BWSlider


_PERSPECTIVE_VERTICAL_KEY = "Perspective_Vertical"
_PERSPECTIVE_HORIZONTAL_KEY = "Perspective_Horizontal"
_STRAIGHTEN_KEY = "Crop_Straighten"
_FLIP_KEY = "Crop_FlipH"


# Aspect ratio presets: label → (w, h) or None for freeform.
_ASPECT_OPTIONS: list[tuple[str, Optional[tuple[int, int]]]] = [
    ("Freeform", None),
    ("Original", None),  # handled specially: uses image aspect ratio
    ("Square", (1, 1)),
    ("16:9", (16, 9)),
    ("4:5", (4, 5)),
    ("5:7", (5, 7)),
    ("4:3", (4, 3)),
    ("3:5", (3, 5)),
    ("3:2", (3, 2)),
]


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

    aspectRatioChanged = Signal(float)
    """Emitted when the user selects a new aspect ratio constraint.

    The value is the locked width/height ratio, or ``0.0`` for freeform.
    A negative value (``-1.0``) means *original* (caller should supply the
    image's native ratio).
    """

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

        # -- Aspect ratio section (below flip) --
        self._aspect_section = _AspectRatioSection(self)
        layout.addWidget(self._aspect_section)

        layout.addStretch(1)

        self._straighten_row.valueChanged.connect(self._on_straighten_changed)
        self._vertical_row.valueChanged.connect(self._on_vertical_value_changed)
        self._horizontal_row.valueChanged.connect(self._on_horizontal_value_changed)
        self._flip_row.toggled.connect(self._on_flip_toggled)
        self._aspect_section.ratioSelected.connect(self.aspectRatioChanged)
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
        layout.setContentsMargins(0, 0, 0, 0)
        # spacing = 24 so the first letter of "Flip" sits at 32 + 24 = 56,
        # matching the "Straighten" / "Aspect" label x position.
        layout.setSpacing(24)

        # Icon inside a 32×32 container to align with _PerspectiveSliderRow icons.
        # The flip SVG fills its canvas with no viewBox padding, so render at a
        # smaller pixmap size to match the visual weight of the slider-row icons.
        icon_label = QLabel(self)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setPixmap(load_icon(icon_name, color=(180, 180, 180)).pixmap(20, 20))
        icon_label.setFixedSize(32, 32)
        icon_label.setCursor(Qt.CursorShape.PointingHandCursor)
        icon_label.mouseReleaseEvent = lambda _ev: self._toggle()
        layout.addWidget(icon_label)
        self._icon_label = icon_label

        # Hidden checkable button used only for checked-state tracking.
        self._button = QToolButton(self)
        self._button.setCheckable(True)
        self._button.setVisible(False)

        self._label_button = QPushButton(label, self)
        self._label_button.setFlat(True)
        self._label_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._label_button.setFont(Edit_SIDEBAR_FONT)
        self._label_button.setStyleSheet("QPushButton { text-align: left; padding: 0; }")
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


class _AspectRatioSection(QWidget):
    """Radio-button list for selecting a crop aspect-ratio constraint.

    Emits ``ratioSelected(float)`` where the value is:
    *  ``0.0``  → freeform (no lock)
    * ``-1.0``  → original (caller resolves to image aspect ratio)
    *  ``> 0``  → explicit width / height ratio
    """

    ratioSelected = Signal(float)

    _STYLESHEET = """
    QRadioButton {
        padding: 4px 2px;
        spacing: 10px;
        background-color: transparent;
        border: none;
        outline: none;
        color: #a0a0a0;
        font-size: 13px;
    }
    QRadioButton:hover { color: #ffffff; }
    QRadioButton:checked { color: #dcdcdc; }
    QRadioButton::indicator { width: 14px; height: 14px; }
    QRadioButton::indicator:unchecked { image: none; }
    QRadioButton::indicator:checked { image: url(CHECKMARK_PATH); }
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(0)

        # Separator line
        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("QFrame { color: #3a3a3c; }")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # Title row — icon in a 32px container; spacing = 24 so the first
        # letter of "Aspect" aligns with the "Straighten" label text inside
        # the BWSlider track.  The text is drawn at track_rect.left() + 10
        # = h_padding(14) + 10 = 24 from BWSlider's left edge, which itself
        # starts right after the 32px icon container → x = 32 + 24 = 56.
        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(0, 8, 0, 4)
        title_layout.setSpacing(24)
        icon_label = QLabel(self)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setPixmap(load_icon("aspect.svg").pixmap(22, 22))
        icon_label.setFixedSize(32, 32)
        title_layout.addWidget(icon_label)
        title_text = QLabel("Aspect", self)
        title_text.setFont(Edit_SIDEBAR_FONT)
        title_layout.addWidget(title_text)
        title_layout.addStretch()
        layout.addLayout(title_layout)

        # Radio buttons — left margin chosen so that the radio button text
        # labels align with the "Straighten" label text at x = 56.  With the
        # stylesheet padding-left (2) + indicator (14) + spacing (10) = 26 px
        # offset from the layout edge, a left margin of 30 puts text at 56.
        options_layout = QVBoxLayout()
        options_layout.setSpacing(0)
        options_layout.setContentsMargins(30, 2, 0, 0)

        # Build check-indicator path for the stylesheet
        check_path = str(_icon_path("checkmark.svg")).replace("\\", "/")
        stylesheet = self._STYLESHEET.replace("CHECKMARK_PATH", check_path)
        self.setStyleSheet(stylesheet)

        self._button_group = QButtonGroup(self)
        # Store the canonical ratio (always ≥ 1 for non-square presets) and
        # the default orientation so that the orientation buttons start in the
        # correct state for each preset.
        self._ratio_map: dict[int, float] = {}
        self._dims_map: dict[int, Optional[tuple[int, int]]] = {}
        self._is_landscape: bool = True  # orientation state

        for idx, (label, dims) in enumerate(_ASPECT_OPTIONS):
            btn = QRadioButton(label, self)
            self._button_group.addButton(btn, idx)
            options_layout.addWidget(btn)
            self._dims_map[idx] = dims

            if dims is None:
                # Freeform → 0.0, Original → -1.0
                self._ratio_map[idx] = 0.0 if label == "Freeform" else -1.0
            else:
                self._ratio_map[idx] = float(dims[0]) / float(dims[1])

            # Default selection: Freeform
            if label == "Freeform":
                btn.setChecked(True)

        layout.addLayout(options_layout)

        # ---- Orientation toggle section (landscape / portrait) ----
        self._orientation_widget = QWidget(self)
        orient_layout = QVBoxLayout(self._orientation_widget)
        orient_layout.setContentsMargins(6, 8, 6, 4)
        orient_layout.setSpacing(6)

        orient_sep = QFrame(self._orientation_widget)
        orient_sep.setFrameShape(QFrame.Shape.HLine)
        orient_sep.setStyleSheet("QFrame { color: #3a3a3c; }")
        orient_sep.setFixedHeight(1)
        orient_layout.addWidget(orient_sep)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        self._landscape_btn = QToolButton(self._orientation_widget)
        self._portrait_btn = QToolButton(self._orientation_widget)
        for btn in (self._landscape_btn, self._portrait_btn):
            btn.setCheckable(True)
            btn.setAutoRaise(True)
            btn.setFixedSize(QSize(48, 48))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QToolButton { border: 1px solid #555; border-radius: 4px; background: transparent; }"
                "QToolButton:checked { border: 1px solid #aaa; }"
            )

        self._update_orientation_icons()

        self._landscape_btn.setChecked(True)

        self._orient_group = QButtonGroup(self._orientation_widget)
        self._orient_group.setExclusive(True)
        self._orient_group.addButton(self._landscape_btn, 0)
        self._orient_group.addButton(self._portrait_btn, 1)

        btn_row.addWidget(self._landscape_btn)
        btn_row.addWidget(self._portrait_btn)
        btn_row.addStretch()
        orient_layout.addLayout(btn_row)

        layout.addWidget(self._orientation_widget)
        self._orientation_widget.setVisible(False)

        self._button_group.idToggled.connect(self._on_button_toggled)
        self._orient_group.idToggled.connect(self._on_orientation_toggled)

    # ------------------------------------------------------------------
    def _make_orientation_pixmap(
        self, *, landscape: bool, checked: bool
    ) -> QPixmap:
        """Render a small pixmap showing a landscape or portrait rectangle.

        The pixmap is created at the application's device-pixel-ratio so
        that it stays crisp on HiDPI / Retina screens.
        """
        size = 44
        # Use the widget's own DPR so pixmaps stay crisp on per-monitor
        # DPI setups where the application-level DPR may differ.
        btn = self._landscape_btn if landscape else self._portrait_btn
        dpr = btn.devicePixelRatioF()
        real = int(size * dpr)
        pix = QPixmap(real, real)
        pix.setDevicePixelRatio(dpr)
        pix.fill(QColor(0, 0, 0, 0))

        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw orientation rectangle
        rect_color = QColor("#c0c0c0") if not checked else QColor("#e0e0e0")
        pen = QPen(rect_color, 1.5)
        painter.setPen(pen)
        painter.setBrush(QColor(0, 0, 0, 0))

        margin = 8
        if landscape:
            # Wider than tall
            rw, rh = size - 2 * margin, int((size - 2 * margin) * 0.65)
        else:
            # Taller than wide
            rw, rh = int((size - 2 * margin) * 0.65), size - 2 * margin
        rx = (size - rw) / 2
        ry = (size - rh) / 2
        painter.drawRoundedRect(QRectF(rx, ry, rw, rh), 2, 2)

        # Draw checkmark indicator when checked – use QIcon.paint() so it
        # renders at the full resolution of the DPR-aware pixmap, avoiding
        # the blurry result that icon.pixmap(small_size) would produce.
        if checked:
            check_icon = load_icon("checkmark.svg", color=(200, 200, 200))
            check_size = 14
            cx = int((size - check_size) / 2)
            cy = int((size - check_size) / 2)
            check_icon.paint(painter, QRect(cx, cy, check_size, check_size))

        painter.end()
        return pix

    def _update_orientation_icons(self) -> None:
        """Refresh the landscape/portrait button icons to reflect state."""
        is_land = self._is_landscape
        land_pix = self._make_orientation_pixmap(landscape=True, checked=is_land)
        port_pix = self._make_orientation_pixmap(landscape=False, checked=not is_land)

        self._landscape_btn.setIcon(QIcon(land_pix))
        self._landscape_btn.setIconSize(QSize(44, 44))
        self._portrait_btn.setIcon(QIcon(port_pix))
        self._portrait_btn.setIconSize(QSize(44, 44))

    # ------------------------------------------------------------------
    def _current_button_id(self) -> int:
        return self._button_group.checkedId()

    def _is_nonsquare_preset(self, button_id: int) -> bool:
        """Return True when the selected preset supports orientation choice."""
        dims = self._dims_map.get(button_id)
        if dims is None:
            return False  # Freeform or Original
        return dims[0] != dims[1]  # not Square

    def _effective_ratio(self, button_id: int) -> float:
        """Return the emitted ratio for *button_id* considering orientation."""
        base_ratio = self._ratio_map.get(button_id, 0.0)
        if base_ratio <= 0:
            return base_ratio  # freeform / original – pass through

        dims = self._dims_map.get(button_id)
        if dims is not None and dims[0] == dims[1]:
            return base_ratio  # square – no orientation

        if self._is_landscape:
            return max(base_ratio, 1.0 / base_ratio)
        else:
            return min(base_ratio, 1.0 / base_ratio)

    def _on_button_toggled(self, button_id: int, checked: bool) -> None:
        if not checked:
            return
        show_orient = self._is_nonsquare_preset(button_id)
        self._orientation_widget.setVisible(show_orient)

        if show_orient:
            # Set default orientation based on preset dimensions.
            # Block orient-group signals while syncing to avoid a second
            # ratioSelected emission from _on_orientation_toggled.
            dims = self._dims_map.get(button_id)
            if dims is not None:
                self._is_landscape = dims[0] >= dims[1]
                self._orient_group.blockSignals(True)
                self._landscape_btn.setChecked(self._is_landscape)
                self._portrait_btn.setChecked(not self._is_landscape)
                self._orient_group.blockSignals(False)
                self._update_orientation_icons()

        self.ratioSelected.emit(self._effective_ratio(button_id))

    def _on_orientation_toggled(self, orient_id: int, checked: bool) -> None:
        if not checked:
            return
        self._is_landscape = orient_id == 0
        self._update_orientation_icons()
        btn_id = self._current_button_id()
        self.ratioSelected.emit(self._effective_ratio(btn_id))

