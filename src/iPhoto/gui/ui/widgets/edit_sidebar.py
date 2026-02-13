"""Composite widget hosting the editing tool sections."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import (
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ....core.bw_resolver import BWParams
from ....core.color_resolver import ColorStats
from ....core.wb_resolver import WBParams
from ..models.edit_session import EditSession
from .edit_perspective_controls import PerspectiveControls
from .edit_sidebar_sections import SECTION_CONFIGS, EditSectionRegistry
from .edit_sidebar_signals import EditSignalRouter
from .edit_section_coordinator import EditSessionCoordinator
from ..palette import SIDEBAR_BACKGROUND_COLOR


class EditSidebar(QWidget):
    """Sidebar that exposes the available editing tools."""

    bwParamsPreviewed = Signal(BWParams)
    """Relays live Black & White adjustments to the controller."""

    bwParamsCommitted = Signal(BWParams)
    """Emitted when Black & White adjustments should be written to the session."""

    wbParamsPreviewed = Signal(WBParams)
    """Relays live White Balance adjustments to the controller."""

    wbParamsCommitted = Signal(WBParams)
    """Emitted when White Balance adjustments should be written to the session."""

    curveParamsPreviewed = Signal(object)
    """Relays live curve adjustments to the controller."""

    curveParamsCommitted = Signal(object)
    """Emitted when curve adjustments should be written to the session."""

    curveEyedropperModeChanged = Signal(object)
    """Relay eyedropper mode toggles from the curve section."""

    selectiveColorParamsPreviewed = Signal(object)
    """Relays live Selective Color adjustments to the controller."""

    selectiveColorParamsCommitted = Signal(object)
    """Emitted when Selective Color adjustments should be written to the session."""

    selectiveColorEyedropperModeChanged = Signal(object)
    """Relay eyedropper mode toggles from the Selective Color section."""

    levelsParamsPreviewed = Signal(object)
    """Relays live levels adjustments to the controller."""

    levelsParamsCommitted = Signal(object)
    """Emitted when levels adjustments should be written to the session."""

    wbEyedropperModeChanged = Signal(object)
    """Relay eyedropper mode toggles from the WB section."""

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
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

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

        # Build all edit sections via the registry.
        self._registry = EditSectionRegistry()
        for i, cfg in enumerate(SECTION_CONFIGS):
            bundle = self._registry.create_section(cfg, scroll_content)
            scroll_layout.addWidget(bundle.container)
            if i < len(SECTION_CONFIGS) - 1:
                scroll_layout.addWidget(EditSectionRegistry.build_separator(scroll_content))

        scroll_layout.addStretch(1)
        scroll_content.setLayout(scroll_layout)
        scroll.setWidget(scroll_content)

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
        crop_layout.addStretch(1)
        crop_container.setLayout(crop_layout)
        self._stack.addWidget(crop_container)

        # Wire relay signals and create the session coordinator.
        EditSignalRouter.connect_section_signals(
            self, self._registry, self._perspective_controls
        )
        self._coordinator = EditSessionCoordinator(
            self, self._registry, self._perspective_controls, parent=self,
        )

        # Expose section widgets and header buttons as instance attributes so that
        # existing code that references them directly (e.g. ``sidebar.light_reset_button``)
        # continues to work without modification.
        _b = self._registry.bundles
        self._light_section = _b["light"].section
        self._color_section = _b["color"].section
        self._bw_section = _b["bw"].section
        self._wb_section = _b["wb"].section
        self._curve_section = _b["curve"].section
        self._levels_section = _b["levels"].section
        self._selective_color_section = _b["selective_color"].section

        self._light_section_container = _b["light"].container
        self._color_section_container = _b["color"].container
        self._bw_section_container = _b["bw"].container
        self._wb_section_container = _b["wb"].container
        self._curve_section_container = _b["curve"].container
        self._levels_section_container = _b["levels"].container
        self._selective_color_section_container = _b["selective_color"].container

        self.light_reset_button = _b["light"].reset_button
        self.light_toggle_button = _b["light"].toggle_button
        self.color_reset_button = _b["color"].reset_button
        self.color_toggle_button = _b["color"].toggle_button
        self.bw_reset_button = _b["bw"].reset_button
        self.bw_toggle_button = _b["bw"].toggle_button
        self.wb_reset_button = _b["wb"].reset_button
        self.wb_toggle_button = _b["wb"].toggle_button
        self.curve_reset_button = _b["curve"].reset_button
        self.curve_toggle_button = _b["curve"].toggle_button
        self.selective_color_reset_button = _b["selective_color"].reset_button
        self.selective_color_toggle_button = _b["selective_color"].toggle_button
        self.levels_reset_button = _b["levels"].reset_button
        self.levels_toggle_button = _b["levels"].toggle_button

        self.set_mode("adjust")

    # ------------------------------------------------------------------
    def set_session(self, session: Optional[EditSession]) -> None:
        """Attach *session* to every tool section."""
        self._coordinator.set_session(session)

    def session(self) -> Optional[EditSession]:
        return self._coordinator.session()

    # ------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        """Switch the visible page to *mode* (``"adjust"`` or ``"crop"``)."""

        index = 0 if mode == "adjust" else 1
        self._stack.setCurrentIndex(index)

    def refresh(self) -> None:
        """Force the currently visible sections to sync with the session."""
        self._coordinator.refresh()

    def set_light_preview_image(
        self,
        image,
        *,
        color_stats: ColorStats | None = None,
    ) -> None:
        """Provide *image* and optional *color_stats* to the edit tool sections."""
        self._coordinator.set_light_preview_image(image, color_stats=color_stats)

    def handle_curve_color_picked(self, r: float, g: float, b: float) -> None:
        """Forward a sampled color to the curve section."""
        self._coordinator.handle_curve_color_picked(r, g, b)

    def handle_wb_color_picked(self, r: float, g: float, b: float) -> None:
        """Forward a sampled colour to the WB section's eyedropper handler."""
        self._coordinator.handle_wb_color_picked(r, g, b)

    def deactivate_wb_eyedropper(self) -> None:
        """Turn off the WB pipette button without emitting a mode-changed signal loop."""
        self._coordinator.deactivate_wb_eyedropper()

    def deactivate_curve_eyedropper(self) -> None:
        """Turn off the Curve eyedropper buttons."""
        self._coordinator.deactivate_curve_eyedropper()

    def handle_selective_color_color_picked(self, r: float, g: float, b: float) -> None:
        """Forward a sampled colour to the Selective Color section's eyedropper handler."""
        self._coordinator.handle_selective_color_color_picked(r, g, b)

    def deactivate_selective_color_eyedropper(self) -> None:
        """Turn off the Selective Color pipette button."""
        self._coordinator.deactivate_selective_color_eyedropper()

    def preview_thumbnail_height(self) -> int:
        """Return the vertical pixel span used by the master thumbnail strips."""
        return self._coordinator.preview_thumbnail_height()

    def set_control_icon_tint(self, color: QColor | None) -> None:
        """Apply a color tint to all header control icons."""
        self._coordinator.set_control_icon_tint(color)
