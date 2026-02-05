"""Update helpers for the detail view header widgets.

This module provides unified header management combining:
- Label updates for location and timestamp display
- Layout management for widget reparenting between detail and edit modes
"""

from __future__ import annotations

from calendar import month_name
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from dateutil.parser import isoparse
from PySide6.QtCore import QAbstractItemModel, QObject, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel

from ..models.roles import Roles

if TYPE_CHECKING:
    from ..ui_main_window import Ui_MainWindow


class HeaderController(QObject):
    """Unified controller for header label updates and layout management.
    
    This controller manages:
    - Location and timestamp labels shown above the player (always available)
    - Widget reparenting between detail header and edit header during mode transitions
      (requires ``ui`` parameter to be set)
    
    The layout management methods (``switch_to_edit_mode`` and ``restore_detail_mode``)
    are no-ops if the ``ui`` parameter was not provided during initialization.
    """

    def __init__(
        self,
        location_label: QLabel,
        timestamp_label: QLabel,
        ui: Optional["Ui_MainWindow"] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        """Initialize header controller with label widgets and optional UI reference.
        
        Args:
            location_label: The label widget for displaying location information.
            timestamp_label: The label widget for displaying timestamp information.
            ui: Optional UI reference for layout management during mode transitions.
            parent: Optional parent QObject for Qt object tree management.
        """
        super().__init__(parent)
        
        self._location_label = location_label
        self._timestamp_label = timestamp_label
        self._ui = ui
        
        self._timestamp_default_font = QFont(self._timestamp_label.font())
        self._timestamp_single_line_font = QFont(self._timestamp_label.font())
        if self._timestamp_single_line_font.pointSize() > 0:
            self._timestamp_single_line_font.setPointSize(
                self._timestamp_single_line_font.pointSize() + 1
            )
        else:
            self._timestamp_single_line_font.setPointSize(14)
        self._timestamp_single_line_font.setBold(True)

    def clear(self) -> None:
        """Clear both labels and hide them from view."""

        self._location_label.clear()
        self._location_label.hide()
        self._timestamp_label.clear()
        self._timestamp_label.hide()
        self._timestamp_label.setFont(self._timestamp_default_font)

    def update_for_row(self, row: Optional[int], model: QAbstractItemModel) -> None:
        """Populate labels with metadata for ``row`` in ``model``."""

        if row is None or row < 0:
            self.clear()
            return
        index = model.index(row, 0)
        if not index.isValid():
            self.clear()
            return
        location_raw = index.data(Roles.LOCATION)
        location_text: Optional[str] = None
        if isinstance(location_raw, str):
            location_text = location_raw.strip() or None
        elif location_raw is not None:
            location_text = str(location_raw).strip() or None
        timestamp_text = self._format_timestamp(index.data(Roles.DT))
        self._apply_header_text(location_text, timestamp_text)

    def _apply_header_text(
        self, location: Optional[str], timestamp: Optional[str]
    ) -> None:
        """Render formatted location and timestamp strings."""

        timestamp = (timestamp or "").strip() or None
        location = (location or "").strip() or None

        if not location and not timestamp:
            self.clear()
            return

        if location:
            self._location_label.setText(location)
            self._location_label.show()
        else:
            self._location_label.clear()
            self._location_label.hide()

        if timestamp:
            if location:
                self._timestamp_label.setFont(self._timestamp_default_font)
            else:
                self._timestamp_label.setFont(self._timestamp_single_line_font)
            self._timestamp_label.setText(timestamp)
            self._timestamp_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._timestamp_label.show()
        else:
            self._timestamp_label.setFont(self._timestamp_single_line_font)
            self._timestamp_label.clear()
            self._timestamp_label.hide()

    def _format_timestamp(self, dt_value: object) -> Optional[str]:
        """Convert ISO-8601 strings into a friendly, localised label."""

        if not dt_value:
            return None
        if isinstance(dt_value, datetime):
            parsed = dt_value
        elif isinstance(dt_value, (int, float)):
            try:
                parsed = datetime.fromtimestamp(float(dt_value), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        elif isinstance(dt_value, str):
            dt_text = dt_value.strip()
            if not dt_text:
                return None
            try:
                parsed = isoparse(dt_text)
            except (ValueError, TypeError):
                for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                    try:
                        parsed = datetime.strptime(dt_text, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    return None
        else:
            return None
        if parsed.tzinfo is None:
            local_tz = datetime.now().astimezone().tzinfo or timezone.utc
            parsed = parsed.replace(tzinfo=local_tz)
        localized = parsed.astimezone()
        month_label = (
            month_name[localized.month] if 0 <= localized.month < len(month_name) else ""
        )
        if not month_label:
            month_label = f"{localized.month:02d}"
        return f"{localized.day}. {month_label}, {localized:%H:%M}"

    # ------------------------------------------------------------------
    # Layout Management (merged from HeaderLayoutManager)
    # ------------------------------------------------------------------
    
    def switch_to_edit_mode(self) -> None:
        """Reparent shared toolbar widgets into the edit header.
        
        Moves zoom widget, info button, and favorite button from the detail
        header to the edit header layout. Requires UI reference to be set.
        """
        if self._ui is None:
            return
            
        ui = self._ui

        # Move Zoom Widget
        if ui.edit_zoom_host_layout.indexOf(ui.zoom_widget) == -1:
            ui.edit_zoom_host_layout.addWidget(ui.zoom_widget)
        ui.zoom_widget.show()

        # Move Info and Favorite buttons
        right_layout = ui.edit_right_controls_layout
        if right_layout.indexOf(ui.info_button) == -1:
            # Insert at beginning to match desired order
            right_layout.insertWidget(0, ui.info_button)
        if right_layout.indexOf(ui.favorite_button) == -1:
            right_layout.insertWidget(1, ui.favorite_button)

    def restore_detail_mode(self) -> None:
        """Return shared toolbar widgets to the detail header layout.
        
        Restores zoom widget, info button, and favorite button to their
        original positions in the detail header. Requires UI reference to be set.
        """
        if self._ui is None:
            return
            
        ui = self._ui

        # Restore widgets to their original positions in detail_actions_layout
        # We rely on indices captured/stored in UI setup or assume they are static
        ui.detail_actions_layout.insertWidget(ui.detail_info_button_index, ui.info_button)
        ui.detail_actions_layout.insertWidget(ui.detail_favorite_button_index, ui.favorite_button)

        # Restore Zoom Widget
        ui.detail_header_layout.insertWidget(ui.detail_zoom_widget_index, ui.zoom_widget)
