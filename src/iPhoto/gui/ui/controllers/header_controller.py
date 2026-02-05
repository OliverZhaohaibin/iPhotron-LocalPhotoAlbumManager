"""Unified controller for header text and edit/detail layout transitions."""

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
    """Drive header labels and shared-widget layout swaps for edit mode."""

    def __init__(
        self,
        location_label: Optional[QLabel] = None,
        timestamp_label: Optional[QLabel] = None,
        *,
        ui: Optional["Ui_MainWindow"] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        """Capture labels and UI references used by header-related interactions."""

        super().__init__(parent)

        self._location_label = location_label
        self._timestamp_label = timestamp_label
        self._ui = ui

        self._timestamp_default_font: Optional[QFont] = None
        self._timestamp_single_line_font: Optional[QFont] = None

        if self._timestamp_label is not None:
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

        if self._location_label is None or self._timestamp_label is None:
            return

        self._location_label.clear()
        self._location_label.hide()
        self._timestamp_label.clear()
        self._timestamp_label.hide()
        self._timestamp_label.setFont(self._timestamp_default_font)

    def update_for_row(self, row: Optional[int], model: QAbstractItemModel) -> None:
        """Populate labels with metadata for ``row`` in ``model``."""

        if self._location_label is None or self._timestamp_label is None:
            return

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

        if not timestamp:
            self.clear()
            return
        timestamp = timestamp.strip()
        if not timestamp:
            self.clear()
            return
        location = (location or "").strip() or None
        if location:
            self._location_label.setText(location)
            self._location_label.show()
            if self._timestamp_default_font is not None:
                self._timestamp_label.setFont(self._timestamp_default_font)
        else:
            self._location_label.clear()
            self._location_label.hide()
            if self._timestamp_single_line_font is not None:
                self._timestamp_label.setFont(self._timestamp_single_line_font)
        self._timestamp_label.setText(timestamp)
        self._timestamp_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timestamp_label.show()

    def _format_timestamp(self, dt_value: object) -> Optional[str]:
        """Convert ISO-8601 strings into a friendly, localised label."""

        if not dt_value:
            return None
        if isinstance(dt_value, datetime):
            parsed = dt_value
        elif isinstance(dt_value, str):
            try:
                parsed = isoparse(dt_value)
            except (ValueError, TypeError):
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

    def switch_to_edit_mode(self) -> None:
        """Reparent shared toolbar widgets into the edit header."""

        ui = self._ui
        if ui is None:
            return

        if ui.edit_zoom_host_layout.indexOf(ui.zoom_widget) == -1:
            ui.edit_zoom_host_layout.addWidget(ui.zoom_widget)
        ui.zoom_widget.show()

        right_layout = ui.edit_right_controls_layout
        if right_layout.indexOf(ui.info_button) == -1:
            right_layout.insertWidget(0, ui.info_button)
        if right_layout.indexOf(ui.favorite_button) == -1:
            right_layout.insertWidget(1, ui.favorite_button)

    def restore_detail_mode(self) -> None:
        """Return shared toolbar widgets to the detail header layout."""

        ui = self._ui
        if ui is None:
            return

        ui.detail_actions_layout.insertWidget(ui.detail_info_button_index, ui.info_button)
        ui.detail_actions_layout.insertWidget(ui.detail_favorite_button_index, ui.favorite_button)

        ui.detail_header_layout.insertWidget(ui.detail_zoom_widget_index, ui.zoom_widget)
        ui.zoom_widget.show()
