"""Pre-configured grid view for the gallery layout."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QAbstractItemView, QListView

from .asset_grid import AssetGrid


class GalleryGridView(AssetGrid):
    """Dense icon-mode grid tuned for album browsing."""

    def __init__(self, parent=None) -> None:  # type: ignore[override]
        super().__init__(parent)
        icon_size = QSize(192, 192)
        self._selection_mode_enabled = False
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(icon_size)
        self.setGridSize(QSize(194, 194))
        self.setSpacing(6)
        self.setUniformItemSizes(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setWordWrap(False)
        self.setSelectionRectVisible(False)
        self._apply_scrollbar_style()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            self._apply_scrollbar_style()
        super().changeEvent(event)

    def _apply_scrollbar_style(self) -> None:
        palette = self.palette()
        text_color = palette.color(QPalette.ColorRole.WindowText)
        if text_color.alpha() < 255:
            text_color = QColor(text_color)
            text_color.setAlpha(255)

        track_color = QColor(text_color)
        track_color.setAlpha(0)
        handle_color = QColor(text_color)
        handle_color.setAlpha(160)
        handle_hover_color = QColor(text_color)
        handle_hover_color.setAlpha(255)

        track_hex = track_color.name(QColor.NameFormat.HexArgb)
        handle_hex = handle_color.name(QColor.NameFormat.HexArgb)
        handle_hover_hex = handle_hover_color.name(QColor.NameFormat.HexArgb)

        style = (
            f"QScrollBar:vertical, QScrollBar:horizontal {{\n"
            f"    background-color: {track_hex};\n"
            "    margin: 0px;\n"
            "    padding: 0px;\n"
            "    border: none;\n"
            "    border-radius: 7px;\n"
            "}\n"
            "QScrollBar:vertical {\n"
            "    width: 14px;\n"
            "}\n"
            "QScrollBar:horizontal {\n"
            "    height: 14px;\n"
            "}\n"
            f"QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{\n"
            f"    background-color: {handle_hex};\n"
            "    border-radius: 5px;\n"
            "    margin: 2px;\n"
            "}\n"
            "QScrollBar::handle:vertical {\n"
            "    min-height: 30px;\n"
            "}\n"
            "QScrollBar::handle:horizontal {\n"
            "    min-width: 30px;\n"
            "}\n"
            f"QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{\n"
            f"    background-color: {handle_hover_hex};\n"
            "}\n"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,\n"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {\n"
            "    width: 0px;\n"
            "    height: 0px;\n"
            "    border: none;\n"
            "    background: none;\n"
            "}\n"
            "QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical,\n"
            "QScrollBar::left-arrow:horizontal, QScrollBar::right-arrow:horizontal {\n"
            "    background: none;\n"
            "    border: none;\n"
            "}\n"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,\n"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {\n"
            "    background: none;\n"
            "    border: none;\n"
            "}"
        )
        self.setStyleSheet(style)

    # ------------------------------------------------------------------
    # Selection mode toggling
    # ------------------------------------------------------------------
    def selection_mode_active(self) -> bool:
        """Return ``True`` when multi-selection mode is currently enabled."""

        return self._selection_mode_enabled

    def set_selection_mode_enabled(self, enabled: bool) -> None:
        """Switch between the default single selection and multi-selection mode."""

        desired_state = bool(enabled)
        if self._selection_mode_enabled == desired_state:
            return
        self._selection_mode_enabled = desired_state
        if desired_state:
            self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            self.setSelectionRectVisible(True)
        else:
            self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.setSelectionRectVisible(False)
        # Long-press previews interfere with multi-selection because the delayed
        # activation steals focus from the selection rubber band. Disabling the
        # preview gesture keeps the pointer interactions unambiguous.
        self.set_preview_enabled(not desired_state)
