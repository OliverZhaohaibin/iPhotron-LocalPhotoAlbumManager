"""Composite widget hosting the editing tool sections."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Slot, Signal
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....core.light_resolver import LIGHT_KEYS
from ....core.color_resolver import COLOR_KEYS, ColorStats
from ....core.bw_resolver import BWParams
from ....core.curve_resolver import DEFAULT_CURVE_POINTS
from ..models.edit_session import EditSession
from .edit_light_section import EditLightSection
from .edit_color_section import EditColorSection
from .edit_bw_section import EditBWSection
from .edit_curve_section import EditCurveSection
from .edit_perspective_controls import PerspectiveControls
from .collapsible_section import CollapsibleSection
from ..palette import SIDEBAR_BACKGROUND_COLOR, Edit_SIDEBAR_FONT
from ..icon import load_icon


class EditSidebar(QWidget):
    """Sidebar that exposes the available editing tools."""

    bwParamsPreviewed = Signal(BWParams)
    """Relays live Black & White adjustments to the controller."""

    bwParamsCommitted = Signal(BWParams)
    """Emitted when Black & White adjustments should be written to the session."""

    curveParamsPreviewed = Signal(object)
    """Relays live curve adjustments to the controller."""

    curveParamsCommitted = Signal(object)
    """Emitted when curve adjustments should be written to the session."""

    curveEyedropperModeChanged = Signal(object)
    """Relay eyedropper mode toggles from the curve section."""

    perspectiveInteractionStarted = Signal()
    """Emitted when the user begins dragging a perspective slider."""

    perspectiveInteractionFinished = Signal()
    """Emitted once the user releases a perspective slider."""

    interactionStarted = Signal()
    """Emitted when any edit interaction (slider drag, toggle, reset) begins."""

    interactionFinished = Signal()
    """Emitted when an interaction concludes."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._session: Optional[EditSession] = None
        self._light_preview_image = None
        self._color_stats: ColorStats | None = None
        self._control_icon_tint: QColor | None = None
        # Track whether the Light header controls have active signal bindings so we can
        # disconnect them safely without triggering PySide warnings when no connection exists.
        self._light_controls_connected = False
        self._color_controls_connected = False
        self._bw_controls_connected = False
        self._curve_controls_connected = False

        # Match the classic sidebar chrome so the edit tools retain the soft blue
        # background the rest of the application uses for navigation panes.
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, SIDEBAR_BACKGROUND_COLOR)
        palette.setColor(QPalette.ColorRole.Base, SIDEBAR_BACKGROUND_COLOR)
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._stack = QStackedWidget(self)
        layout.addWidget(self._stack)

        # Adjust page ---------------------------------------------------
        adjust_container = QWidget(self)
        adjust_layout = QVBoxLayout(adjust_container)
        adjust_layout.setContentsMargins(0, 0, 0, 0)
        adjust_layout.setSpacing(0)

        scroll = QScrollArea(adjust_container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        # Ensure the scroll surface shares the same tint so the viewport and the
        # surrounding frame render as a single continuous panel.
        scroll_palette = scroll.palette()
        scroll_palette.setColor(QPalette.ColorRole.Base, SIDEBAR_BACKGROUND_COLOR)
        scroll_palette.setColor(QPalette.ColorRole.Window, SIDEBAR_BACKGROUND_COLOR)
        scroll.setPalette(scroll_palette)

        scroll_content = QWidget(scroll)
        # Allow the scroll area content to compress to zero width during the edit transition.  The
        # animated splitter reduces the sidebar to a sliver before hiding it entirely, so the
        # interior widget hierarchy must advertise that no minimum space is required; otherwise Qt
        # clamps the collapse and the sidebar appears to "pop" out of existence.
        scroll_content.setMinimumWidth(0)
        scroll_content_palette = scroll_content.palette()
        scroll_content_palette.setColor(QPalette.ColorRole.Window, SIDEBAR_BACKGROUND_COLOR)
        scroll_content_palette.setColor(QPalette.ColorRole.Base, SIDEBAR_BACKGROUND_COLOR)
        scroll_content.setPalette(scroll_content_palette)
        scroll_content.setAutoFillBackground(True)
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(12, 12, 12, 12)
        scroll_layout.setSpacing(12)

        self._light_section = EditLightSection(scroll_content)
        self._light_section_container = CollapsibleSection(
            "Light",
            "sun.max.svg",
            self._light_section,
            scroll_content,
            title_font=Edit_SIDEBAR_FONT
        )

        self.light_reset_button = QToolButton(self._light_section_container)
        self.light_reset_button.setAutoRaise(True)
        self.light_reset_button.setIcon(load_icon("arrow.uturn.left.svg"))
        self.light_reset_button.setToolTip("Reset Light adjustments")

        self.light_toggle_button = QToolButton(self._light_section_container)
        self.light_toggle_button.setAutoRaise(True)
        self.light_toggle_button.setCheckable(True)
        self.light_toggle_button.setIcon(load_icon("circle.svg"))
        self.light_toggle_button.setToolTip("Toggle Light adjustments")

        self._light_section_container.add_header_control(self.light_reset_button)
        self._light_section_container.add_header_control(self.light_toggle_button)

        self._light_section.interactionStarted.connect(self.interactionStarted)
        self._light_section.interactionFinished.connect(self.interactionFinished)

        scroll_layout.addWidget(self._light_section_container)

        scroll_layout.addWidget(self._build_separator(scroll_content))

        self._color_section = EditColorSection(scroll_content)
        self._color_section_container = CollapsibleSection(
            "Color",
            "color.circle.svg",
            self._color_section,
            scroll_content,
            title_font=Edit_SIDEBAR_FONT
        )

        self.color_reset_button = QToolButton(self._color_section_container)
        self.color_reset_button.setAutoRaise(True)
        self.color_reset_button.setIcon(load_icon("arrow.uturn.left.svg"))
        self.color_reset_button.setToolTip("Reset Color adjustments")

        self.color_toggle_button = QToolButton(self._color_section_container)
        self.color_toggle_button.setAutoRaise(True)
        self.color_toggle_button.setCheckable(True)
        self.color_toggle_button.setIcon(load_icon("circle.svg"))
        self.color_toggle_button.setToolTip("Toggle Color adjustments")

        self._color_section_container.add_header_control(self.color_reset_button)
        self._color_section_container.add_header_control(self.color_toggle_button)

        self._color_section.interactionStarted.connect(self.interactionStarted)
        self._color_section.interactionFinished.connect(self.interactionFinished)

        scroll_layout.addWidget(self._color_section_container)

        scroll_layout.addWidget(self._build_separator(scroll_content))

        self._bw_section = EditBWSection(scroll_content)
        self._bw_section_container = CollapsibleSection(
            "Black & White",
            "circle.lefthalf.fill.svg",
            self._bw_section,
            scroll_content,
            title_font=Edit_SIDEBAR_FONT,
        )

        self.bw_reset_button = QToolButton(self._bw_section_container)
        self.bw_reset_button.setAutoRaise(True)
        self.bw_reset_button.setIcon(load_icon("arrow.uturn.left.svg"))
        self.bw_reset_button.setToolTip("Reset Black & White adjustments")
        self.bw_toggle_button = QToolButton(self._bw_section_container)
        self.bw_toggle_button.setAutoRaise(True)
        self.bw_toggle_button.setCheckable(True)
        self.bw_toggle_button.setIcon(load_icon("circle.svg"))
        self.bw_toggle_button.setToolTip("Toggle Black & White adjustments")
        self._bw_section_container.add_header_control(self.bw_reset_button)
        self._bw_section_container.add_header_control(self.bw_toggle_button)

        scroll_layout.addWidget(self._bw_section_container)

        scroll_layout.addWidget(self._build_separator(scroll_content))

        # Curve section
        self._curve_section = EditCurveSection(scroll_content)
        self._curve_section_container = CollapsibleSection(
            "Curve",
            "curve.svg",
            self._curve_section,
            scroll_content,
            title_font=Edit_SIDEBAR_FONT,
        )

        self.curve_reset_button = QToolButton(self._curve_section_container)
        self.curve_reset_button.setAutoRaise(True)
        self.curve_reset_button.setIcon(load_icon("arrow.uturn.left.svg"))
        self.curve_reset_button.setToolTip("Reset Curve adjustments")
        self.curve_toggle_button = QToolButton(self._curve_section_container)
        self.curve_toggle_button.setAutoRaise(True)
        self.curve_toggle_button.setCheckable(True)
        self.curve_toggle_button.setIcon(load_icon("circle.svg"))
        self.curve_toggle_button.setToolTip("Toggle Curve adjustments")
        self._curve_section_container.add_header_control(self.curve_reset_button)
        self._curve_section_container.add_header_control(self.curve_toggle_button)

        scroll_layout.addWidget(self._curve_section_container)

        self._curve_section.curveParamsPreviewed.connect(self.curveParamsPreviewed)
        self._curve_section.curveParamsCommitted.connect(self.curveParamsCommitted)
        self._curve_section.interactionStarted.connect(self.interactionStarted)
        self._curve_section.interactionFinished.connect(self.interactionFinished)
        self._curve_section.eyedropperModeChanged.connect(self.curveEyedropperModeChanged)

        scroll_layout.addStretch(1)
        scroll_content.setLayout(scroll_layout)
        scroll.setWidget(scroll_content)

        self._bw_section.paramsPreviewed.connect(self.bwParamsPreviewed)
        self._bw_section.paramsCommitted.connect(self.bwParamsCommitted)
        self._bw_section.interactionStarted.connect(self.interactionStarted)
        self._bw_section.interactionFinished.connect(self.interactionFinished)

        adjust_layout.addWidget(scroll)
        adjust_container.setLayout(adjust_layout)
        self._stack.addWidget(adjust_container)

        # Crop page -----------------------------------------------------
        crop_container = QWidget(self)
        crop_palette = crop_container.palette()
        crop_palette.setColor(QPalette.ColorRole.Window, SIDEBAR_BACKGROUND_COLOR)
        crop_palette.setColor(QPalette.ColorRole.Base, SIDEBAR_BACKGROUND_COLOR)
        crop_container.setPalette(crop_palette)
        crop_container.setAutoFillBackground(True)
        crop_layout = QVBoxLayout(crop_container)
        crop_layout.setContentsMargins(24, 24, 24, 24)
        self._perspective_controls = PerspectiveControls(crop_container)
        crop_layout.addWidget(self._perspective_controls)
        self._perspective_controls.interactionStarted.connect(self.perspectiveInteractionStarted)
        self._perspective_controls.interactionFinished.connect(self.perspectiveInteractionFinished)
        # Also connect to generic interaction signals
        self._perspective_controls.interactionStarted.connect(self.interactionStarted)
        self._perspective_controls.interactionFinished.connect(self.interactionFinished)
        crop_layout.addStretch(1)
        crop_container.setLayout(crop_layout)
        self._stack.addWidget(crop_container)

        self.set_mode("adjust")

    # ------------------------------------------------------------------
    def set_session(self, session: Optional[EditSession]) -> None:
        """Attach *session* to every tool section."""

        if self._light_controls_connected:
            # Only attempt to disconnect signal handlers when they are known to be bound.
            # PySide prints RuntimeWarnings if disconnect() is invoked without a matching
            # connection, so guarding the call keeps the debug console clean when the editor
            # is repeatedly opened and closed.
            try:
                self.light_reset_button.clicked.disconnect(self._on_light_reset)
            except (TypeError, RuntimeError):
                # If Qt reports that the slot is already disconnected we simply clear the flag.
                pass
            try:
                self.light_toggle_button.toggled.disconnect(self._on_light_toggled)
            except (TypeError, RuntimeError):
                pass

            if self._session is not None:
                try:
                    self._session.valueChanged.disconnect(self._on_session_value_changed)
                except (TypeError, RuntimeError):
                    pass

            self._light_controls_connected = False

        if self._color_controls_connected:
            try:
                self.color_reset_button.clicked.disconnect(self._on_color_reset)
            except (TypeError, RuntimeError):
                pass
            try:
                self.color_toggle_button.toggled.disconnect(self._on_color_toggled)
            except (TypeError, RuntimeError):
                pass
            self._color_controls_connected = False

        if self._bw_controls_connected:
            try:
                self.bw_reset_button.clicked.disconnect(self._on_bw_reset)
            except (TypeError, RuntimeError):
                pass
            try:
                self.bw_toggle_button.toggled.disconnect(self._on_bw_toggled)
            except (TypeError, RuntimeError):
                pass
            self._bw_controls_connected = False

        if self._curve_controls_connected:
            try:
                self.curve_reset_button.clicked.disconnect(self._on_curve_reset)
            except (TypeError, RuntimeError):
                pass
            try:
                self.curve_toggle_button.toggled.disconnect(self._on_curve_toggled)
            except (TypeError, RuntimeError):
                pass
            self._curve_controls_connected = False

        self._session = session
        self._light_section.bind_session(session)
        self._color_section.bind_session(session)
        self._bw_section.bind_session(session)
        self._curve_section.bind_session(session)
        self._perspective_controls.bind_session(session)
        if session is not None:
            self.light_reset_button.clicked.connect(self._on_light_reset)
            self.light_toggle_button.toggled.connect(self._on_light_toggled)
            self.color_reset_button.clicked.connect(self._on_color_reset)
            self.color_toggle_button.toggled.connect(self._on_color_toggled)
            session.valueChanged.connect(self._on_session_value_changed)
            self._light_controls_connected = True
            self._color_controls_connected = True
            self.bw_reset_button.clicked.connect(self._on_bw_reset)
            self.bw_toggle_button.toggled.connect(self._on_bw_toggled)
            self._bw_controls_connected = True
            self.bw_reset_button.setEnabled(True)
            self.curve_reset_button.clicked.connect(self._on_curve_reset)
            self.curve_toggle_button.toggled.connect(self._on_curve_toggled)
            self._curve_controls_connected = True
            self.curve_reset_button.setEnabled(True)
            self._sync_light_toggle_state()
            self._sync_color_toggle_state()
            self._sync_bw_toggle_state()
            self._sync_curve_toggle_state()
            if self._light_preview_image is not None:
                self._light_section.set_preview_image(self._light_preview_image)
                self._color_section.set_preview_image(
                    self._light_preview_image,
                    color_stats=self._color_stats,
                )
                self._bw_section.set_preview_image(self._light_preview_image)
        else:
            self.light_toggle_button.setChecked(False)
            self._update_light_toggle_icon(False)
            self.color_toggle_button.setChecked(False)
            self._update_color_toggle_icon(False)
            self._color_stats = None
            self.bw_reset_button.setEnabled(False)
            self.bw_toggle_button.setChecked(False)
            self._update_bw_toggle_icon(False)
            self.curve_reset_button.setEnabled(False)
            self.curve_toggle_button.setChecked(False)
            self._update_curve_toggle_icon(False)

    def session(self) -> Optional[EditSession]:
        return self._session

    # ------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        """Switch the visible page to *mode* (``"adjust"`` or ``"crop"``)."""

        index = 0 if mode == "adjust" else 1
        self._stack.setCurrentIndex(index)

    def refresh(self) -> None:
        """Force the currently visible sections to sync with the session."""

        self._light_section.refresh_from_session()
        self._color_section.refresh_from_session()
        self._bw_section.refresh_from_session()
        self._curve_section.refresh_from_session()
        self._perspective_controls.refresh_from_session()
        self._sync_light_toggle_state()
        self._sync_color_toggle_state()
        self._sync_bw_toggle_state()
        self._sync_curve_toggle_state()

    def set_light_preview_image(
        self,
        image,
        *,
        color_stats: ColorStats | None = None,
    ) -> None:
        """Provide *image* and optional *color_stats* to the edit tool sections."""

        self._light_preview_image = image
        self._color_stats = color_stats
        self._light_section.set_preview_image(image)
        self._color_section.set_preview_image(image, color_stats=color_stats)
        self._bw_section.set_preview_image(image)
        self._curve_section.set_preview_image(image)

    def handle_curve_color_picked(self, r: float, g: float, b: float) -> None:
        """Forward a sampled color to the curve section."""

        self._curve_section.handle_color_picked(r, g, b)

    def preview_thumbnail_height(self) -> int:
        """Return the vertical pixel span used by the master thumbnail strips."""

        return max(
            self._light_section.master_slider.track_height(),
            self._color_section.master_slider.track_height(),
        )

    def _build_separator(self, parent: QWidget) -> QFrame:
        """Return a subtle divider separating adjacent section headers."""

        separator = QFrame(parent)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Plain)
        separator.setStyleSheet("QFrame { background-color: palette(mid); }")
        separator.setFixedHeight(1)
        return separator

    def _on_light_reset(self) -> None:
        if self._session is None:
            return
        self.interactionStarted.emit()
        updates = {key: 0.0 for key in LIGHT_KEYS}
        updates["Light_Master"] = 0.0
        updates["Light_Enabled"] = True
        self._session.set_values(updates)
        self._light_section.refresh_from_session()
        self._sync_light_toggle_state()
        self.interactionFinished.emit()

    def _on_light_toggled(self, checked: bool) -> None:
        self._update_light_toggle_icon(checked)
        if self._session is None:
            return
        self.interactionStarted.emit()
        self._session.set_value("Light_Enabled", checked)
        self.interactionFinished.emit()

    def _on_color_reset(self) -> None:
        if self._session is None:
            return
        self.interactionStarted.emit()
        updates = {key: 0.0 for key in COLOR_KEYS}
        updates["Color_Master"] = 0.0
        updates["Color_Enabled"] = True
        self._session.set_values(updates)
        self._color_section.refresh_from_session()
        self._sync_color_toggle_state()
        self.interactionFinished.emit()

    def _on_color_toggled(self, checked: bool) -> None:
        self._update_color_toggle_icon(checked)
        if self._session is None:
            return
        self.interactionStarted.emit()
        self._session.set_value("Color_Enabled", checked)
        self.interactionFinished.emit()

    def _on_bw_reset(self) -> None:
        if self._session is None:
            return
        self.interactionStarted.emit()
        updates = {
            "BW_Master": 0.5,
            "BW_Intensity": 0.5,
            "BW_Neutrals": 0.0,
            "BW_Tone": 0.0,
            "BW_Grain": 0.0,
            "BW_Enabled": False,
        }
        self._session.set_values(updates)
        self._bw_section.refresh_from_session()
        self._sync_bw_toggle_state()
        self.interactionFinished.emit()

    def _on_bw_toggled(self, checked: bool) -> None:
        self._update_bw_toggle_icon(checked)
        if self._session is None:
            return
        self.interactionStarted.emit()
        self._session.set_value("BW_Enabled", checked)
        self.interactionFinished.emit()

    @Slot(str, object)  # Use object to match (float | bool)
    def _on_session_value_changed(self, key: str, value: object) -> None:
        """Listen for session changes (e.g., from sliders) to sync the toggle button."""
        del value  # We only care about the key, not the specific value
        if key == "Light_Enabled":
            self._sync_light_toggle_state()
        if key == "Color_Enabled":
            self._sync_color_toggle_state()
        if key == "BW_Enabled":
            self._sync_bw_toggle_state()
        if key == "Curve_Enabled":
            self._sync_curve_toggle_state()

    def _sync_light_toggle_state(self) -> None:
        if self._session is None:
            block = self.light_toggle_button.blockSignals(True)
            self.light_toggle_button.setChecked(False)
            self.light_toggle_button.blockSignals(block)
            self._update_light_toggle_icon(False)
            return
        enabled = bool(self._session.value("Light_Enabled"))
        block = self.light_toggle_button.blockSignals(True)
        self.light_toggle_button.setChecked(enabled)
        self.light_toggle_button.blockSignals(block)
        self._update_light_toggle_icon(enabled)

    def _update_light_toggle_icon(self, enabled: bool) -> None:
        if enabled:
            self.light_toggle_button.setIcon(load_icon("checkmark.circle.svg"))
        else:
            tint_name = self._control_icon_tint.name(QColor.NameFormat.HexArgb) if self._control_icon_tint else None
            self.light_toggle_button.setIcon(load_icon("circle.svg", color=tint_name))

    def _sync_color_toggle_state(self) -> None:
        if self._session is None:
            block = self.color_toggle_button.blockSignals(True)
            self.color_toggle_button.setChecked(False)
            self.color_toggle_button.blockSignals(block)
            self._update_color_toggle_icon(False)
            return
        enabled = bool(self._session.value("Color_Enabled"))
        block = self.color_toggle_button.blockSignals(True)
        self.color_toggle_button.setChecked(enabled)
        self.color_toggle_button.blockSignals(block)
        self._update_color_toggle_icon(enabled)

    def _update_color_toggle_icon(self, enabled: bool) -> None:
        if enabled:
            self.color_toggle_button.setIcon(load_icon("checkmark.circle.svg"))
        else:
            tint_name = (
                self._control_icon_tint.name(QColor.NameFormat.HexArgb)
                if self._control_icon_tint
                else None
            )
            self.color_toggle_button.setIcon(load_icon("circle.svg", color=tint_name))

    def _sync_bw_toggle_state(self) -> None:
        if self._session is None:
            block = self.bw_toggle_button.blockSignals(True)
            self.bw_toggle_button.setChecked(False)
            self.bw_toggle_button.blockSignals(block)
            self._update_bw_toggle_icon(False)
            return
        enabled = bool(self._session.value("BW_Enabled"))
        block = self.bw_toggle_button.blockSignals(True)
        self.bw_toggle_button.setChecked(enabled)
        self.bw_toggle_button.blockSignals(block)
        self._update_bw_toggle_icon(enabled)

    def _update_bw_toggle_icon(self, enabled: bool) -> None:
        if enabled:
            self.bw_toggle_button.setIcon(load_icon("checkmark.circle.svg"))
        else:
            tint_name = (
                self._control_icon_tint.name(QColor.NameFormat.HexArgb)
                if self._control_icon_tint
                else None
            )
            self.bw_toggle_button.setIcon(load_icon("circle.svg", color=tint_name))

    def _on_curve_reset(self) -> None:
        if self._session is None:
            return
        self.interactionStarted.emit()
        updates = {
            "Curve_Enabled": False,
            "Curve_RGB": list(DEFAULT_CURVE_POINTS),
            "Curve_Red": list(DEFAULT_CURVE_POINTS),
            "Curve_Green": list(DEFAULT_CURVE_POINTS),
            "Curve_Blue": list(DEFAULT_CURVE_POINTS),
        }
        self._session.set_values(updates)
        self._curve_section.refresh_from_session()
        self._sync_curve_toggle_state()
        self.interactionFinished.emit()

    def _on_curve_toggled(self, checked: bool) -> None:
        self._update_curve_toggle_icon(checked)
        if self._session is None:
            return
        self.interactionStarted.emit()
        self._session.set_value("Curve_Enabled", checked)
        self.interactionFinished.emit()

    def _sync_curve_toggle_state(self) -> None:
        if self._session is None:
            block = self.curve_toggle_button.blockSignals(True)
            self.curve_toggle_button.setChecked(False)
            self.curve_toggle_button.blockSignals(block)
            self._update_curve_toggle_icon(False)
            return
        enabled = bool(self._session.value("Curve_Enabled"))
        block = self.curve_toggle_button.blockSignals(True)
        self.curve_toggle_button.setChecked(enabled)
        self.curve_toggle_button.blockSignals(block)
        self._update_curve_toggle_icon(enabled)

    def _update_curve_toggle_icon(self, enabled: bool) -> None:
        if enabled:
            self.curve_toggle_button.setIcon(load_icon("checkmark.circle.svg"))
        else:
            tint_name = (
                self._control_icon_tint.name(QColor.NameFormat.HexArgb)
                if self._control_icon_tint
                else None
            )
            self.curve_toggle_button.setIcon(load_icon("circle.svg", color=tint_name))


    def set_control_icon_tint(self, color: QColor | None) -> None:
        """Apply a color tint to all header control icons."""
        self._control_icon_tint = color
        tint_name = color.name(QColor.NameFormat.HexArgb) if color else None
        self.light_reset_button.setIcon(
            load_icon("arrow.uturn.left.svg", color=tint_name)
        )
        self.color_reset_button.setIcon(
            load_icon("arrow.uturn.left.svg", color=tint_name)
        )
        self.bw_reset_button.setIcon(
            load_icon("arrow.uturn.left.svg", color=tint_name)
        )
        self.curve_reset_button.setIcon(
            load_icon("arrow.uturn.left.svg", color=tint_name)
        )
        self._sync_light_toggle_state()
        self._sync_color_toggle_state()
        self._sync_bw_toggle_state()
        self._sync_curve_toggle_state()
