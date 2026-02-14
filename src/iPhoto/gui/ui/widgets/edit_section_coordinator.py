"""Edit session coordination for the sidebar."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QColor

from ....core.light_resolver import LIGHT_KEYS
from ....core.color_resolver import COLOR_KEYS, ColorStats
from ....core.curve_resolver import DEFAULT_CURVE_POINTS
from ....core.levels_resolver import DEFAULT_LEVELS_HANDLES
from ....core.selective_color_resolver import DEFAULT_SELECTIVE_COLOR_RANGES
from ..models.edit_session import EditSession
from ..icon import load_icon

if TYPE_CHECKING:
    from .edit_sidebar import EditSidebar
    from .edit_sidebar_sections import EditSectionRegistry
    from .edit_perspective_controls import PerspectiveControls


class EditSessionCoordinator(QObject):
    """Manages the edit session lifecycle, reset/toggle/sync and icon-update logic.

    This object is owned by :class:`EditSidebar` and should not be instantiated
    independently.
    """

    # Maps section key → session-enabled key
    _ENABLED_KEYS: dict[str, str] = {
        "light": "Light_Enabled",
        "color": "Color_Enabled",
        "bw": "BW_Enabled",
        "wb": "WB_Enabled",
        "curve": "Curve_Enabled",
        "selective_color": "SelectiveColor_Enabled",
        "levels": "Levels_Enabled",
    }
    _ENABLED_KEY_TO_SECTION: dict[str, str] = {v: k for k, v in _ENABLED_KEYS.items()}

    def __init__(
        self,
        sidebar: EditSidebar,
        registry: EditSectionRegistry,
        perspective_controls: PerspectiveControls,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._sidebar = sidebar
        self._registry = registry
        self._perspective_controls = perspective_controls
        self._session: Optional[EditSession] = None
        self._light_preview_image = None
        self._color_stats: ColorStats | None = None
        self._control_icon_tint: QColor | None = None
        self._connected: dict[str, bool] = {key: False for key in registry.bundles}

    # -- helpers -----------------------------------------------------------

    def _bundle(self, key: str):
        return self._registry.bundles[key]

    def _section(self, key: str):
        return self._bundle(key).section

    def _reset_btn(self, key: str):
        return self._bundle(key).reset_button

    def _toggle_btn(self, key: str):
        return self._bundle(key).toggle_button

    # -- public API --------------------------------------------------------

    def session(self) -> Optional[EditSession]:
        return self._session

    def set_session(self, session: Optional[EditSession]) -> None:
        """Attach *session* to every tool section."""

        self._disconnect_all()

        self._session = session

        for bundle in self._registry.bundles.values():
            bundle.section.bind_session(session)
        self._perspective_controls.bind_session(session)

        if session is not None:
            self._connect_all(session)
        else:
            self._clear_all_toggles()

    def refresh(self) -> None:
        """Force the currently visible sections to sync with the session."""

        for bundle in self._registry.bundles.values():
            bundle.section.refresh_from_session()
        self._perspective_controls.refresh_from_session()
        self._sync_all_toggle_states()

    def set_light_preview_image(
        self,
        image,
        *,
        color_stats: ColorStats | None = None,
    ) -> None:
        """Provide *image* and optional *color_stats* to the edit tool sections."""

        self._light_preview_image = image
        self._color_stats = color_stats
        self._section("light").set_preview_image(image)
        self._section("color").set_preview_image(image, color_stats=color_stats)
        self._section("bw").set_preview_image(image)
        self._section("curve").set_preview_image(image)
        self._section("levels").set_preview_image(image)

    def handle_curve_color_picked(self, r: float, g: float, b: float) -> None:
        """Forward a sampled color to the curve section."""
        self._section("curve").handle_color_picked(r, g, b)

    def handle_wb_color_picked(self, r: float, g: float, b: float) -> None:
        """Forward a sampled colour to the WB section's eyedropper handler."""
        self._section("wb").handle_color_picked(r, g, b)

    def deactivate_wb_eyedropper(self) -> None:
        """Turn off the WB pipette button without emitting a mode-changed signal loop."""
        self._section("wb").deactivate_eyedropper()

    def deactivate_curve_eyedropper(self) -> None:
        """Turn off the Curve eyedropper buttons."""
        self._section("curve").deactivate_eyedropper()

    def handle_selective_color_color_picked(self, r: float, g: float, b: float) -> None:
        """Forward a sampled colour to the Selective Color section's eyedropper handler."""
        self._section("selective_color").handle_color_picked(r, g, b)

    def deactivate_selective_color_eyedropper(self) -> None:
        """Turn off the Selective Color pipette button."""
        self._section("selective_color").deactivate_eyedropper()

    def preview_thumbnail_height(self) -> int:
        """Return the vertical pixel span used by the master thumbnail strips."""
        return max(
            self._section("light").master_slider.track_height(),
            self._section("color").master_slider.track_height(),
        )

    def set_control_icon_tint(self, color: QColor | None) -> None:
        """Apply a color tint to all header control icons."""

        self._control_icon_tint = color
        tint_name = color.name(QColor.NameFormat.HexArgb) if color else None
        for bundle in self._registry.bundles.values():
            bundle.reset_button.setIcon(load_icon("arrow.uturn.left.svg", color=tint_name))
        self._sync_all_toggle_states()

    # -- connection management --------------------------------------------

    _RESET_SLOT_ATTR = {
        "light": "_on_light_reset",
        "color": "_on_color_reset",
        "bw": "_on_bw_reset",
        "wb": "_on_wb_reset",
        "curve": "_on_curve_reset",
        "selective_color": "_on_selective_color_reset",
        "levels": "_on_levels_reset",
    }
    _TOGGLE_SLOT_ATTR = {
        "light": "_on_light_toggled",
        "color": "_on_color_toggled",
        "bw": "_on_bw_toggled",
        "wb": "_on_wb_toggled",
        "curve": "_on_curve_toggled",
        "selective_color": "_on_selective_color_toggled",
        "levels": "_on_levels_toggled",
    }

    def _disconnect_all(self) -> None:
        """Disconnect all header-button signals that are currently connected."""

        for key in self._registry.bundles:
            if not self._connected.get(key, False):
                continue
            try:
                self._reset_btn(key).clicked.disconnect(getattr(self, self._RESET_SLOT_ATTR[key]))
            except (TypeError, RuntimeError):
                pass
            try:
                self._toggle_btn(key).toggled.disconnect(
                    getattr(self, self._TOGGLE_SLOT_ATTR[key])
                )
            except (TypeError, RuntimeError):
                pass
            self._connected[key] = False

        if self._session is not None:
            try:
                self._session.valueChanged.disconnect(self._on_session_value_changed)
            except (TypeError, RuntimeError):
                pass

    def _connect_all(self, session: EditSession) -> None:
        """Connect all header-button signals for an active session."""

        for key in self._registry.bundles:
            self._reset_btn(key).clicked.connect(getattr(self, self._RESET_SLOT_ATTR[key]))
            self._toggle_btn(key).toggled.connect(getattr(self, self._TOGGLE_SLOT_ATTR[key]))
            self._connected[key] = True
            self._reset_btn(key).setEnabled(True)

        session.valueChanged.connect(self._on_session_value_changed)
        self._sync_all_toggle_states()

        if self._light_preview_image is not None:
            self._section("light").set_preview_image(self._light_preview_image)
            self._section("color").set_preview_image(
                self._light_preview_image,
                color_stats=self._color_stats,
            )
            self._section("bw").set_preview_image(self._light_preview_image)

    def _clear_all_toggles(self) -> None:
        """Reset all toggle buttons to unchecked state when session is ``None``."""

        for key in self._registry.bundles:
            self._toggle_btn(key).setChecked(False)
            self._update_toggle_icon(key, False)
        for key in ("bw", "wb", "curve", "selective_color", "levels"):
            self._reset_btn(key).setEnabled(False)
        self._color_stats = None

    # -- toggle state sync ------------------------------------------------

    def _sync_all_toggle_states(self) -> None:
        for key in self._registry.bundles:
            self._sync_toggle_state(key)

    def _sync_toggle_state(self, key: str) -> None:
        toggle = self._toggle_btn(key)
        if self._session is None:
            block = toggle.blockSignals(True)
            toggle.setChecked(False)
            toggle.blockSignals(block)
            self._update_toggle_icon(key, False)
            return
        enabled = bool(self._session.value(self._ENABLED_KEYS[key]))
        block = toggle.blockSignals(True)
        toggle.setChecked(enabled)
        toggle.blockSignals(block)
        self._update_toggle_icon(key, enabled)

    def _update_toggle_icon(self, key: str, enabled: bool) -> None:
        toggle = self._toggle_btn(key)
        if enabled:
            toggle.setIcon(load_icon("checkmark.circle.svg"))
        else:
            tint_name = (
                self._control_icon_tint.name(QColor.NameFormat.HexArgb)
                if self._control_icon_tint
                else None
            )
            toggle.setIcon(load_icon("circle.svg", color=tint_name))

    # -- reset handlers ---------------------------------------------------

    def _on_light_reset(self) -> None:
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        updates: dict[str, object] = {key: 0.0 for key in LIGHT_KEYS}
        updates["Light_Master"] = 0.0
        updates["Light_Enabled"] = True
        self._session.set_values(updates)
        self._section("light").refresh_from_session()
        self._sync_toggle_state("light")
        self._sidebar.interactionFinished.emit()

    def _on_color_reset(self) -> None:
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        updates: dict[str, object] = {key: 0.0 for key in COLOR_KEYS}
        updates["Color_Master"] = 0.0
        updates["Color_Enabled"] = True
        self._session.set_values(updates)
        self._section("color").refresh_from_session()
        self._sync_toggle_state("color")
        self._sidebar.interactionFinished.emit()

    def _on_bw_reset(self) -> None:
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        updates = {
            "BW_Master": 0.5,
            "BW_Intensity": 0.5,
            "BW_Neutrals": 0.0,
            "BW_Tone": 0.0,
            "BW_Grain": 0.0,
            "BW_Enabled": False,
        }
        self._session.set_values(updates)
        self._section("bw").refresh_from_session()
        self._sync_toggle_state("bw")
        self._sidebar.interactionFinished.emit()

    def _on_wb_reset(self) -> None:
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        updates = {
            "WB_Warmth": 0.0,
            "WB_Temperature": 0.0,
            "WB_Tint": 0.0,
            "WB_Enabled": False,
        }
        self._session.set_values(updates)
        self._section("wb").refresh_from_session()
        self._sync_toggle_state("wb")
        self._sidebar.interactionFinished.emit()

    def _on_curve_reset(self) -> None:
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        updates = {
            "Curve_Enabled": False,
            "Curve_RGB": list(DEFAULT_CURVE_POINTS),
            "Curve_Red": list(DEFAULT_CURVE_POINTS),
            "Curve_Green": list(DEFAULT_CURVE_POINTS),
            "Curve_Blue": list(DEFAULT_CURVE_POINTS),
        }
        self._session.set_values(updates)
        self._section("curve").refresh_from_session()
        self._sync_toggle_state("curve")
        self._sidebar.interactionFinished.emit()

    def _on_selective_color_reset(self) -> None:
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        updates = {
            "SelectiveColor_Enabled": False,
            "SelectiveColor_Ranges": [list(r) for r in DEFAULT_SELECTIVE_COLOR_RANGES],
        }
        self._session.set_values(updates)
        self._section("selective_color").refresh_from_session()
        self._sync_toggle_state("selective_color")
        self._sidebar.interactionFinished.emit()

    def _on_levels_reset(self) -> None:
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        updates = {
            "Levels_Enabled": False,
            "Levels_Handles": list(DEFAULT_LEVELS_HANDLES),
        }
        self._session.set_values(updates)
        self._section("levels").refresh_from_session()
        self._sync_toggle_state("levels")
        self._sidebar.interactionFinished.emit()

    # -- toggle handlers --------------------------------------------------

    def _on_light_toggled(self, checked: bool) -> None:
        self._update_toggle_icon("light", checked)
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        self._session.set_value("Light_Enabled", checked)
        self._sidebar.interactionFinished.emit()

    def _on_color_toggled(self, checked: bool) -> None:
        self._update_toggle_icon("color", checked)
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        self._session.set_value("Color_Enabled", checked)
        self._sidebar.interactionFinished.emit()

    def _on_bw_toggled(self, checked: bool) -> None:
        self._update_toggle_icon("bw", checked)
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        self._session.set_value("BW_Enabled", checked)
        self._sidebar.interactionFinished.emit()

    def _on_wb_toggled(self, checked: bool) -> None:
        self._update_toggle_icon("wb", checked)
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        self._session.set_value("WB_Enabled", checked)
        self._sidebar.interactionFinished.emit()

    def _on_curve_toggled(self, checked: bool) -> None:
        self._update_toggle_icon("curve", checked)
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        self._session.set_value("Curve_Enabled", checked)
        self._sidebar.interactionFinished.emit()

    def _on_selective_color_toggled(self, checked: bool) -> None:
        self._update_toggle_icon("selective_color", checked)
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        self._session.set_value("SelectiveColor_Enabled", checked)
        self._sidebar.interactionFinished.emit()

    def _on_levels_toggled(self, checked: bool) -> None:
        self._update_toggle_icon("levels", checked)
        if self._session is None:
            return
        self._sidebar.interactionStarted.emit()
        self._session.set_value("Levels_Enabled", checked)
        self._sidebar.interactionFinished.emit()

    # -- session value change handler -------------------------------------

    @Slot(str, object)
    def _on_session_value_changed(self, key: str, value: object) -> None:
        """Listen for session changes to sync the corresponding toggle button."""
        del value
        section_key = self._ENABLED_KEY_TO_SECTION.get(key)
        if section_key is not None:
            self._sync_toggle_state(section_key)
