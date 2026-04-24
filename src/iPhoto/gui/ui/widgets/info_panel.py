"""Floating window that displays EXIF metadata for the selected asset."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Optional

from PySide6.QtCore import QDateTime, QEvent, QLocale, QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPalette, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from iPhoto.people.repository import AssetFaceAnnotation, PersonSummary

from ..icons import load_icon
from ..menus.core import MenuActionSpec, MenuContext, populate_menu
from ..menus.style import apply_menu_style
from .info_location_map import InfoLocationMapView
from .main_window_metrics import TITLE_BAR_HEIGHT, WINDOW_CONTROL_BUTTON_SIZE, WINDOW_CONTROL_GLYPH_SIZE
from .people_dashboard_dialogs import GroupPeopleDialog

# Matches a lens string that is already a self-contained spec with *both* a
# focal-length component ("23mm", "24-70mm") *and* an aperture component
# ("f/2", "f/3.5").  A bare lens-model name like "XF23mmF2 R WR" or an iPhone
# string like "back camera 4.2mm f/1.6" both qualify, but so does a formatted
# LensInfo spec like "23mm f/2".  When the full pattern matches we skip
# appending the separate focal/aperture EXIF fields to avoid duplication.
# Note: requires an explicit "f/" prefix so that "XF23mmF2" alone does NOT
# trigger the early return — the "F2" token there is not preceded by "f/".
_LENS_SPEC_RE = re.compile(
    r"\d+(?:[.\-]\d+)?mm"   # focal length part, e.g. "23mm" or "24-70mm"
    r".*?"                   # any characters in between
    r"\bf/\d+(?:\.\d+)?",   # aperture part, e.g. "f/2" or "f/3.5"
    re.IGNORECASE,
)

_PLUS_CIRCLE_ICON_PATH = Path(__file__).resolve().parents[1] / "icon" / "plus.circle.svg"
_FACE_AVATAR_DIAMETER = 48


def _parse_svg_dimension(value: str) -> float:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", value)
    return float(match.group(1)) if match is not None else 0.0


def _measure_svg_path_bounds(svg_path: Path) -> tuple[float, float, float, float]:
    try:
        text = svg_path.read_text(encoding="utf-8")
    except OSError:
        return (0.0, 0.0, 0.0, 0.0)
    path_match = re.search(r"d='([^']+)'", text, re.DOTALL)
    if path_match is None:
        return (0.0, 0.0, 0.0, 0.0)
    numbers = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", path_match.group(1))]
    if len(numbers) < 2:
        return (0.0, 0.0, 0.0, 0.0)
    xs = numbers[0::2]
    ys = numbers[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


def _face_add_button_metrics() -> tuple[QSize, QSize]:
    try:
        text = _PLUS_CIRCLE_ICON_PATH.read_text(encoding="utf-8")
    except OSError:
        fallback = QSize(_FACE_AVATAR_DIAMETER, _FACE_AVATAR_DIAMETER)
        return fallback, fallback
    width_match = re.search(r"width='([^']+)'", text)
    height_match = re.search(r"height='([^']+)'", text)
    svg_width = _parse_svg_dimension(width_match.group(1)) if width_match is not None else 0.0
    svg_height = _parse_svg_dimension(height_match.group(1)) if height_match is not None else 0.0
    min_x, min_y, max_x, max_y = _measure_svg_path_bounds(_PLUS_CIRCLE_ICON_PATH)
    outer_width = max(1.0, max_x - min_x)
    outer_height = max(1.0, max_y - min_y)
    if svg_width <= 0.0 or svg_height <= 0.0:
        fallback = QSize(_FACE_AVATAR_DIAMETER, _FACE_AVATAR_DIAMETER)
        return fallback, fallback
    scale = max(_FACE_AVATAR_DIAMETER / outer_width, _FACE_AVATAR_DIAMETER / outer_height)
    icon_size = QSize(
        max(_FACE_AVATAR_DIAMETER, int(round(svg_width * scale))),
        max(_FACE_AVATAR_DIAMETER, int(round(svg_height * scale))),
    )
    button_side = max(icon_size.width(), icon_size.height())
    return icon_size, QSize(button_side, button_side)


_FACE_ADD_ICON_SIZE, _FACE_ADD_BUTTON_SIZE = _face_add_button_metrics()


def _style_popup_input_dialog(dialog: QInputDialog, parent: QWidget | None) -> None:
    palette = parent.palette() if parent is not None else QPalette()
    bg = palette.color(QPalette.ColorRole.Window).name()
    text_col = palette.color(QPalette.ColorRole.WindowText).name()
    base = palette.color(QPalette.ColorRole.Base).name()
    text_input = palette.color(QPalette.ColorRole.Text).name()
    button = palette.color(QPalette.ColorRole.Button).name()
    button_text = palette.color(QPalette.ColorRole.ButtonText).name()
    dialog.setStyleSheet(
        f"""
        QInputDialog {{
            background-color: {bg};
            color: {text_col};
        }}
        QLabel {{
            color: {text_col};
        }}
        QLineEdit, QComboBox, QListView {{
            background-color: {base};
            color: {text_input};
            border: 1px solid {text_col};
            padding: 4px;
        }}
        QPushButton {{
            background-color: {button};
            color: {button_text};
            border: 1px solid {text_col};
            padding: 6px 16px;
            min-width: 60px;
        }}
        QPushButton:hover {{
            background-color: {base};
        }}
        """
    )


def _uses_dark_theme(widget: QWidget | None) -> bool:
    host = widget.window() if widget is not None and widget.window() is not None else widget
    coordinator = getattr(host, "coordinator", None)
    context = getattr(coordinator, "_context", None)
    theme_manager = getattr(context, "theme", None)
    if theme_manager is not None and hasattr(theme_manager, "get_effective_theme_mode"):
        return theme_manager.get_effective_theme_mode() == "dark"

    settings = getattr(context, "settings", None)
    if settings is not None and hasattr(settings, "get"):
        theme_setting = settings.get("ui.theme", "system")
        if theme_setting == "dark":
            return True
        if theme_setting == "light":
            return False

    app = QGuiApplication.instance()
    if app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
        return True

    palette_source = host.palette() if host is not None else QPalette()
    return palette_source.color(QPalette.ColorRole.Window).lightness() < 128


class _FaceAvatarWidget(QLabel):
    deleteRequested = Signal(object)
    moveRequested = Signal(object, str)
    renameRequested = Signal(object, str)

    _ACTIVE_BORDER_COLOR = "#0A84FF"
    _PLACEHOLDER_STYLE = (
        f"background-color: rgba(207, 214, 225, 220); border-radius: {_FACE_AVATAR_DIAMETER // 2}px;"
    )

    def __init__(
        self,
        annotation: AssetFaceAnnotation,
        candidates: list[PersonSummary],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._annotation = annotation
        self._candidates = list(candidates)
        self._is_menu_active = False
        self._is_placeholder = False
        self.setFixedSize(_FACE_AVATAR_DIAMETER, _FACE_AVATAR_DIAMETER)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_visual_state()

    def set_candidates(self, candidates: list[PersonSummary]) -> None:
        self._candidates = list(candidates)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = self._build_context_menu()
        if menu is None:
            event.ignore()
            return
        self._set_menu_active(True)
        menu.aboutToHide.connect(lambda: self._set_menu_active(False))
        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        if chosen.text() == "Delete":
            self.deleteRequested.emit(self._annotation)
            return
        if chosen.text() == "Choose Someone Else…":
            self._prompt_choose_person()
            return
        if chosen.text() == "Rename Person…":
            self._prompt_rename_person()

    def _build_context_menu(self) -> QMenu | None:
        delete_label, not_this_label, submenu_labels = self._menu_action_labels()
        menu = QMenu(self)
        apply_menu_style(menu, self)
        context = MenuContext(
            surface="info_panel",
            selection_kind="empty",
            entity_kind="person",
            entity_id=self._annotation.person_id,
        )
        populate_menu(
            menu,
            context=context,
            action_specs=[
                MenuActionSpec(
                    action_id="delete_face",
                    label=delete_label,
                ),
                MenuActionSpec(
                    action_id="not_this_person",
                    label=not_this_label,
                    children=tuple(
                        MenuActionSpec(
                            action_id=f"not_this_person:{submenu_label}",
                            label=submenu_label,
                        )
                        for submenu_label in submenu_labels
                    ),
                ),
            ],
            anchor=self,
        )
        submenu = menu.actions()[1].menu() if len(menu.actions()) > 1 else None
        if submenu is not None:
            menu._face_action_submenu = submenu  # type: ignore[attr-defined]
        return menu

    def _menu_action_labels(self) -> tuple[str, str, tuple[str, str]]:
        return ("Delete", self._not_this_label(), ("Choose Someone Else…", "Rename Person…"))

    def _not_this_label(self) -> str:
        display_name = str(self._annotation.display_name or "").strip()
        return f"Not {display_name}" if display_name else "Not This Person"

    def _prompt_choose_person(self) -> None:
        options = [
            summary
            for summary in self._candidates
            if summary.person_id and summary.person_id != self._annotation.person_id
        ]
        if not options:
            return
        host = self.window() if isinstance(self.window(), QWidget) else self
        dialog = GroupPeopleDialog(
            options,
            title_text="Choose Someone Else",
            prompt_text="Assign this face to",
            confirm_text="Choose",
            min_selection=1,
            max_selection=1,
            dark_mode=_uses_dark_theme(host),
            parent=host,
        )
        if dialog.exec() != QInputDialog.DialogCode.Accepted:
            return
        selected_ids = dialog.selected_person_ids()
        if not selected_ids:
            return
        self.moveRequested.emit(self._annotation, selected_ids[0])

    def _prompt_rename_person(self) -> None:
        host = self.window() if isinstance(self.window(), QWidget) else self
        dialog = QInputDialog(host)
        dialog.setWindowTitle("Rename Person")
        dialog.setLabelText("New name:")
        dialog.setTextValue("")
        _style_popup_input_dialog(dialog, host)
        if dialog.exec() != QInputDialog.DialogCode.Accepted:
            return
        new_name = dialog.textValue().strip()
        if not new_name:
            return
        self.renameRequested.emit(self._annotation, new_name)

    def _set_menu_active(self, active: bool) -> None:
        self._is_menu_active = bool(active)
        self._refresh_visual_state()

    def _refresh_visual_state(self) -> None:
        pixmap = _avatar_pixmap(self._annotation.thumbnail_path)
        border = (
            f"border: 2px solid {self._ACTIVE_BORDER_COLOR};"
            if self._is_menu_active
            else "border: none;"
        )
        if pixmap is not None:
            self._is_placeholder = False
            self.setPixmap(pixmap)
            self.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setStyleSheet(
                f"QLabel {{ background: transparent; border-radius: {_FACE_AVATAR_DIAMETER // 2}px; {border} }}"
            )
            return
        self._is_placeholder = True
        self.clear()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText(" ")
        self.setStyleSheet(
            f"QLabel {{ {self._PLACEHOLDER_STYLE} {border} }}"
        )


def _person_choice_label(summary: PersonSummary) -> str:
    display_name = str(summary.name or "").strip() or "Unnamed"
    return f"{display_name} ({summary.face_count})"

@dataclass
class _FormattedMetadata:
    """Pre-formatted strings used to populate the info panel labels."""

    name: str = ""
    timestamp: str = ""
    camera: str = ""
    lens: str = ""
    summary: str = ""
    exposure_line: str = ""
    is_video: bool = False


class InfoPanel(QWidget):
    """Small helper window that mirrors macOS Photos' info popover.

    The panel uses a frameless rounded window with a custom title bar
    whose close button reuses the main window's ``red.close.circle.svg``
    glyph for visual consistency.
    """

    _CORNER_RADIUS = 12.0
    _SHADOW_SIZE = 16
    _SHADOW_MAX_ALPHA = 18
    _SHADOW_RADIUS_GROWTH = 0.5
    dismissed = Signal()
    manualFaceAddRequested = Signal()
    faceDeleteRequested = Signal(object)
    faceMoveRequested = Signal(object, str)
    faceMoveToNewPersonRequested = Signal(object, str)
    locationQueryChanged = Signal(str)
    locationSuggestionActivated = Signal(object)
    locationConfirmRequested = Signal(str, object)
    _DRAG_EVENT_TYPES = frozenset(
        (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
        ),
    )

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumWidth(320)

        self._metadata: Optional[dict[str, Any]] = None
        self._current_rel: Optional[str] = None
        self._asset_faces: list[AssetFaceAnnotation] = []
        self._face_action_candidates: list[PersonSummary] = []
        self._drag_active = False
        self._drag_offset = None
        self._centered = False
        self._post_show_reflow_queued = False
        self._post_show_reflow_recenter = False
        self._location_capability_enabled = False
        self._location_fallback_text = "Install the map extension to use Assign a Location."
        self._location_suggestions: list[object] = []
        self._selected_location_suggestion: object | None = None
        self._updating_location_ui = False
        self._location_dirty = False
        self._location_confirm_queued = False

        # -- title bar -----------------------------------------------------
        self._title_bar = QWidget(self)
        self._title_bar.setFixedHeight(TITLE_BAR_HEIGHT)
        title_layout = QHBoxLayout(self._title_bar)
        title_layout.setContentsMargins(16, 10, 12, 6)
        title_layout.setSpacing(8)

        self._title_label = QLabel("Info", self._title_bar)
        self._title_label.setObjectName("infoPanelTitleLabel")
        self._title_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        )
        self._title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        self._title_label.installEventFilter(self)
        title_layout.addWidget(self._title_label, 1)

        self._close_button = QToolButton(self._title_bar)
        self._close_button.setIcon(load_icon("red.close.circle.svg"))
        self._close_button.setIconSize(WINDOW_CONTROL_GLYPH_SIZE)
        self._close_button.setFixedSize(WINDOW_CONTROL_BUTTON_SIZE)
        self._close_button.setAutoRaise(True)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._close_button.setToolTip("Close")
        self._apply_close_button_style()
        self._close_button.clicked.connect(self.close)
        title_layout.addWidget(
            self._close_button, 0, Qt.AlignmentFlag.AlignRight,
        )
        self._title_bar.installEventFilter(self)

        # -- content labels ------------------------------------------------
        self._filename_label = self._make_content_label()
        self._timestamp_label = self._make_content_label()
        self._camera_label = self._make_content_label()
        self._lens_label = self._make_content_label()
        self._summary_label = self._make_content_label()
        self._exposure_label = self._make_content_label()

        # -- root layout ---------------------------------------------------
        s = self._SHADOW_SIZE
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, s, s)
        layout.setSpacing(0)
        layout.addWidget(self._title_bar)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 8, 16, 16)
        content_layout.setSpacing(12)
        content_layout.addWidget(self._filename_label)
        content_layout.addWidget(self._timestamp_label)

        metadata_frame = QWidget(self)
        metadata_layout = QVBoxLayout(metadata_frame)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_layout.setSpacing(6)
        metadata_layout.addWidget(self._camera_label)
        metadata_layout.addWidget(self._lens_label)
        metadata_layout.addWidget(self._summary_label)
        content_layout.addWidget(metadata_frame)

        separator = QFrame(self)
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        content_layout.addWidget(separator)

        exposure_container = QWidget(self)
        exposure_layout = QHBoxLayout(exposure_container)
        exposure_layout.setContentsMargins(0, 0, 0, 0)
        exposure_layout.addWidget(self._exposure_label)
        content_layout.addWidget(exposure_container)

        self._face_separator = QFrame(self)
        self._face_separator.setFrameShape(QFrame.HLine)
        self._face_separator.setFrameShadow(QFrame.Sunken)
        content_layout.addWidget(self._face_separator)

        self._face_container = QWidget(self)
        self._face_layout = QHBoxLayout(self._face_container)
        self._face_layout.setContentsMargins(0, 0, 0, 0)
        self._face_layout.setSpacing(8)
        self._face_layout.addStretch(1)
        self._face_add_button = QToolButton(self._face_container)
        self._face_add_button.setIconSize(_FACE_ADD_ICON_SIZE)
        self._face_add_button.setFixedSize(_FACE_ADD_BUTTON_SIZE)
        self._face_add_button.setAutoRaise(True)
        self._face_add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._face_add_button.setStyleSheet(
            "QToolButton { padding: 0px; margin: 0px; border: none; background: transparent; }"
        )
        self._face_add_button.clicked.connect(self.manualFaceAddRequested.emit)
        self._update_face_add_button_icon()
        content_layout.addWidget(self._face_container)

        self._location_separator = QFrame(self)
        self._location_separator.setFrameShape(QFrame.HLine)
        self._location_separator.setFrameShadow(QFrame.Sunken)
        content_layout.addWidget(self._location_separator)

        self._location_container = QWidget(self)
        self._location_layout = QVBoxLayout(self._location_container)
        self._location_layout.setContentsMargins(0, 0, 0, 0)
        self._location_layout.setSpacing(8)

        self._location_fallback_label = QLabel(self._location_container)
        self._location_fallback_label.setWordWrap(True)
        self._location_fallback_label.hide()
        self._location_layout.addWidget(self._location_fallback_label)

        self._location_editor_row = QWidget(self._location_container)
        self._location_editor_layout = QHBoxLayout(self._location_editor_row)
        self._location_editor_layout.setContentsMargins(0, 0, 0, 0)
        self._location_editor_layout.setSpacing(8)

        self._location_editor = QLineEdit(self._location_editor_row)
        self._location_editor.setClearButtonEnabled(True)
        self._location_editor.setCursor(Qt.CursorShape.IBeamCursor)
        self._location_editor.setPlaceholderText("Assign a Location")
        self._location_editor.installEventFilter(self)
        self._location_editor.textEdited.connect(self._handle_location_text_edited)
        self._location_editor_layout.addWidget(self._location_editor, 1)

        self._location_confirm_button = QPushButton("Confirm", self._location_editor_row)
        self._location_confirm_button.setAutoDefault(False)
        self._location_confirm_button.setDefault(False)
        self._location_confirm_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._location_confirm_button.setEnabled(False)
        self._location_confirm_button.clicked.connect(self._emit_location_confirm_requested)
        self._location_editor_layout.addWidget(self._location_confirm_button, 0)
        self._location_layout.addWidget(self._location_editor_row)

        self._location_results = QListWidget(self._location_container)
        self._location_results.setAlternatingRowColors(True)
        self._location_results.setMaximumHeight(150)
        self._location_results.hide()
        self._location_results.installEventFilter(self)
        self._location_results.itemClicked.connect(self._handle_location_item_clicked)
        self._location_results.itemActivated.connect(self._handle_location_item_activated)
        self._location_results.currentRowChanged.connect(self._handle_location_row_changed)
        self._location_layout.addWidget(self._location_results)

        self._location_map = InfoLocationMapView(self._location_container)
        self._location_map.hide()
        self._location_layout.addWidget(self._location_map)
        content_layout.addWidget(self._location_container)

        content_layout.addStretch(1)
        layout.addWidget(content, 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_asset_metadata(self, metadata: Mapping[str, Any]) -> None:
        """Populate the panel with information extracted from *metadata*."""

        previous_rel = self._current_rel
        self._metadata = dict(metadata)
        self._current_rel = str(metadata.get("rel") or metadata.get("name") or "") or None

        formatted = self._format_metadata(metadata)
        self._filename_label.setText(formatted.name)
        self._timestamp_label.setText(formatted.timestamp)
        self._camera_label.setVisible(bool(formatted.camera))
        self._camera_label.setText(formatted.camera)
        self._lens_label.setVisible(bool(formatted.lens))
        self._lens_label.setText(formatted.lens)
        self._summary_label.setVisible(bool(formatted.summary))
        self._summary_label.setText(formatted.summary)
        if formatted.exposure_line:
            self._exposure_label.setText(formatted.exposure_line)
        else:
            is_loading = bool(metadata.get("_metadata_loading"))
            fallback = (
                "Loading detailed video information..."
                if formatted.is_video and is_loading
                else "Loading detailed exposure information..."
                if is_loading
                else "Detailed video information is unavailable."
                if formatted.is_video
                else "Detailed exposure information is unavailable."
            )
            self._exposure_label.setText(fallback)
        self._apply_location_metadata(metadata, previous_rel=previous_rel)
        self._refresh_panel_geometry()
        if self.isVisible():
            self._schedule_post_show_reflow(recenter=False)

    def set_asset_faces(self, annotations: list[AssetFaceAnnotation]) -> None:
        self._asset_faces = list(annotations)
        self._rebuild_face_strip()
        self._refresh_panel_geometry()
        if self.isVisible():
            self._schedule_post_show_reflow(recenter=False)

    def set_face_action_candidates(self, candidates: list[PersonSummary]) -> None:
        self._face_action_candidates = list(candidates)
        self._rebuild_face_strip()

    def clear(self) -> None:
        """Reset the panel to an empty state without hiding the window."""

        self._metadata = None
        self._current_rel = None
        self._face_action_candidates = []
        for label in (
            self._filename_label,
            self._timestamp_label,
            self._camera_label,
            self._lens_label,
            self._summary_label,
            self._exposure_label,
        ):
            label.clear()
        self._exposure_label.setText("No metadata available for this item.")
        self._clear_location_results()
        self._location_map.clear_location()
        self._location_map.hide()
        self._location_fallback_label.hide()
        self._location_editor_row.setVisible(self._location_capability_enabled)
        self._location_editor.clear()
        self._location_editor.setPlaceholderText("Assign a Location")
        self._location_editor.setReadOnly(False)
        self._location_editor.setClearButtonEnabled(True)
        self._location_confirm_button.setEnabled(False)
        self._location_confirm_button.setText("Confirm")
        self._location_dirty = False
        self._location_confirm_queued = False
        self.set_asset_faces([])
        self._refresh_panel_geometry()
        if self.isVisible():
            self._schedule_post_show_reflow(recenter=False)

    def current_rel(self) -> Optional[str]:
        """Return the relative path associated with the displayed asset."""

        return self._current_rel

    @property
    def close_button(self) -> QToolButton:
        """Expose the close button for external signal wiring."""

        return self._close_button

    def set_location_capability(self, *, enabled: bool, fallback_text: str | None = None) -> None:
        self._location_capability_enabled = bool(enabled)
        if isinstance(fallback_text, str) and fallback_text.strip():
            self._location_fallback_text = fallback_text.strip()
        self._apply_location_metadata(self._metadata or {}, previous_rel=self._current_rel)
        self._refresh_panel_geometry()

    def set_location_suggestions(self, suggestions: list[object]) -> None:
        self._location_suggestions = list(suggestions)
        self._location_results.clear()
        if not self._location_capability_enabled or not self._location_suggestions:
            self._selected_location_suggestion = None
            self._location_results.hide()
            self._location_confirm_button.setEnabled(False)
            self._refresh_panel_geometry()
            return

        for index, suggestion in enumerate(self._location_suggestions):
            display_name = str(getattr(suggestion, "display_name", "") or "").strip()
            secondary_text = str(getattr(suggestion, "secondary_text", "") or "").strip()
            if not display_name:
                continue
            item = QListWidgetItem(display_name if not secondary_text else f"{display_name}\n{secondary_text}")
            item.setToolTip(secondary_text)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self._location_results.addItem(item)

        if self._location_results.count() <= 0:
            self._selected_location_suggestion = None
            self._location_results.hide()
            self._location_confirm_button.setEnabled(False)
            self._refresh_panel_geometry()
            return

        self._location_results.setCurrentRow(0)
        self._location_results.show()
        self._location_confirm_button.setEnabled(True)
        self._refresh_panel_geometry()

    def set_location_busy(self, busy: bool) -> None:
        self._location_editor.setEnabled(self._location_capability_enabled)
        self._location_editor.setReadOnly(bool(busy) and self._location_capability_enabled)
        self._location_editor.setClearButtonEnabled(self._location_capability_enabled and not busy)
        self._location_confirm_button.setEnabled(
            not busy and self._location_capability_enabled and self._selected_location_suggestion is not None
        )
        self._location_confirm_button.setText("Assigning..." if busy else "Confirm")

    def preview_location(self, display_name: str, latitude: float, longitude: float) -> None:
        metadata = dict(self._metadata or {})
        metadata["location"] = display_name
        metadata["place"] = display_name
        metadata["gps"] = {
            "lat": float(latitude),
            "lon": float(longitude),
        }
        self._metadata = metadata
        self._updating_location_ui = True
        try:
            self._location_editor.setText(display_name)
        finally:
            self._updating_location_ui = False
        self._location_dirty = False
        self._location_editor.setPlaceholderText("")
        self._clear_location_results()
        self._location_map.set_location(float(latitude), float(longitude))
        self._location_map.show()
        self._refresh_panel_geometry()

    def shutdown(self) -> None:
        self._location_map.shutdown()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _apply_close_button_style(self) -> None:
        """Recompute hover/pressed colours from the current palette."""
        text = self.palette().color(QPalette.ColorRole.WindowText)
        hover = QColor(text)
        hover.setAlpha(20)
        pressed = QColor(text)
        pressed.setAlpha(35)
        self._close_button.setStyleSheet(
            "QToolButton { background: transparent; border: none; }"
            f"QToolButton:hover {{ background-color: {hover.name(QColor.NameFormat.HexArgb)}; border-radius: 6px; }}"
            f"QToolButton:pressed {{ background-color: {pressed.name(QColor.NameFormat.HexArgb)}; border-radius: 6px; }}"
        )

    def _resolve_action_icon_tint(self) -> str | None:
        color = self.palette().color(QPalette.ColorRole.ButtonText)
        if not color.isValid():
            color = self.palette().color(QPalette.ColorRole.WindowText)
        if not color.isValid():
            return None
        return color.name(QColor.NameFormat.HexArgb)

    def _update_face_add_button_icon(self) -> None:
        self._face_add_button.setIcon(
            load_icon(
                "plus.circle.svg",
                color=self._resolve_action_icon_tint(),
                size=(_FACE_ADD_ICON_SIZE.width(), _FACE_ADD_ICON_SIZE.height()),
            )
        )

    def _make_content_label(self) -> QLabel:
        """Create a word-wrapped plain-text label with stable vertical sizing."""

        label = QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        return label

    def _apply_location_metadata(
        self,
        metadata: Mapping[str, Any],
        *,
        previous_rel: str | None,
    ) -> None:
        if previous_rel != self._current_rel:
            self._clear_location_results()
            self._location_dirty = False
        if not self._location_capability_enabled:
            self._clear_location_results()
            self._location_editor_row.hide()
            self._location_results.hide()
            self._location_map.hide()
            self._location_fallback_label.setText(self._location_fallback_text)
            self._location_fallback_label.show()
            return

        self._location_fallback_label.hide()
        self._location_editor_row.show()
        gps = metadata.get("gps")
        location_text = metadata.get("location") or metadata.get("place")
        normalized_location = (
            str(location_text).strip()
            if isinstance(location_text, str) and str(location_text).strip()
            else ""
        )
        should_replace_editor_text = (
            previous_rel != self._current_rel
            or (not self._location_editor.hasFocus() and not self._location_dirty)
        )
        if should_replace_editor_text:
            self._updating_location_ui = True
            try:
                self._location_editor.setText(normalized_location)
            finally:
                self._updating_location_ui = False
            self._location_dirty = False
        self._location_editor.setPlaceholderText(
            "Assign a Location" if not normalized_location else ""
        )

        if isinstance(gps, dict):
            latitude = gps.get("lat")
            longitude = gps.get("lon")
            if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
                self._location_map.set_location(float(latitude), float(longitude))
                self._location_map.show()
            else:
                self._location_map.clear_location()
                self._location_map.hide()
        else:
            self._location_map.clear_location()
            self._location_map.hide()

    def _handle_location_text_edited(self, text: str) -> None:
        if self._updating_location_ui:
            return
        self._location_dirty = True
        self._selected_location_suggestion = None
        self._location_confirm_button.setEnabled(False)
        if not text.strip():
            self._clear_location_results()
        self.locationQueryChanged.emit(text)

    def _handle_location_item_clicked(self, item: QListWidgetItem) -> None:
        self._select_location_item(item, update_editor=True)

    def _handle_location_item_activated(self, item: QListWidgetItem) -> None:
        self._select_location_item(item, update_editor=True)
        self._queue_location_confirm_requested()

    def _handle_location_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._location_suggestions):
            self._selected_location_suggestion = None
            self._location_confirm_button.setEnabled(False)
            return
        suggestion = self._location_suggestions[row]
        self._selected_location_suggestion = suggestion
        self.locationSuggestionActivated.emit(suggestion)
        self._location_confirm_button.setEnabled(True)

    def _select_location_item(self, item: QListWidgetItem, *, update_editor: bool) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or index < 0 or index >= len(self._location_suggestions):
            return
        self._location_results.setCurrentItem(item)
        suggestion = self._location_suggestions[index]
        self._selected_location_suggestion = suggestion
        if update_editor:
            display_name = str(getattr(suggestion, "display_name", "") or "").strip()
            if display_name:
                self._updating_location_ui = True
                try:
                    self._location_editor.setText(display_name)
                finally:
                    self._updating_location_ui = False
                self._location_editor.setFocus(Qt.FocusReason.OtherFocusReason)
        self._location_dirty = True
        self.locationSuggestionActivated.emit(suggestion)
        self._location_confirm_button.setEnabled(True)

    def _queue_location_confirm_requested(self) -> None:
        if self._location_confirm_queued:
            return
        self._location_confirm_queued = True
        QTimer.singleShot(0, self._emit_location_confirm_requested)

    def _emit_location_confirm_requested(self) -> None:
        self._location_confirm_queued = False
        if not self._location_capability_enabled:
            return
        query = self._location_editor.text().strip()
        if not query:
            return
        suggestion = self._selected_location_suggestion
        if suggestion is None and self._location_results.count() > 0:
            row = self._location_results.currentRow()
            if row < 0:
                row = 0
            if 0 <= row < len(self._location_suggestions):
                suggestion = self._location_suggestions[row]
        if suggestion is None:
            return
        self._clear_location_results()
        self.locationConfirmRequested.emit(query, suggestion)

    def _clear_location_results(self) -> None:
        self._location_suggestions = []
        self._selected_location_suggestion = None
        self._location_results.clear()
        self._location_results.hide()
        self._location_confirm_button.setEnabled(False)

    def _handle_location_editor_key_press(self, event: QKeyEvent) -> bool:
        key = event.key()
        if key not in (
            Qt.Key.Key_Down,
            Qt.Key.Key_Up,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Escape,
        ):
            return False
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._queue_location_confirm_requested()
            return True
        if self._location_results.count() <= 0:
            return key == Qt.Key.Key_Escape
        if key == Qt.Key.Key_Escape:
            self._clear_location_results()
            return True

        current_row = self._location_results.currentRow()
        if current_row < 0:
            current_row = 0
        if key == Qt.Key.Key_Down:
            current_row = min(self._location_results.count() - 1, current_row + 1)
        else:
            current_row = max(0, current_row - 1)
        self._location_results.setCurrentRow(current_row)
        return True

    def _handle_location_results_key_press(self, event: QKeyEvent) -> bool:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._queue_location_confirm_requested()
            return True
        if key == Qt.Key.Key_Escape:
            self._clear_location_results()
            self._location_editor.setFocus(Qt.FocusReason.OtherFocusReason)
            return True
        return False

    def _rebuild_face_strip(self) -> None:
        while self._face_layout.count() > 0:
            item = self._face_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if widget is self._face_add_button:
                    widget.hide()
                else:
                    widget.deleteLater()
        for annotation in self._asset_faces:
            self._face_layout.addWidget(self._make_face_avatar(annotation))
        self._face_add_button.show()
        self._face_layout.addWidget(self._face_add_button)
        self._face_layout.addStretch(1)
        self._face_separator.setVisible(True)
        self._face_container.setVisible(True)

    def _make_face_avatar(self, annotation: AssetFaceAnnotation) -> QLabel:
        label = _FaceAvatarWidget(annotation, self._face_action_candidates, self._face_container)
        label.deleteRequested.connect(self.faceDeleteRequested.emit)
        label.moveRequested.connect(self.faceMoveRequested.emit)
        label.renameRequested.connect(self.faceMoveToNewPersonRequested.emit)
        return label

    def _refresh_panel_geometry(self, *, recenter: bool = False) -> None:
        """Recompute the preferred panel geometry after content changes."""

        self.ensurePolished()
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self.updateGeometry()
        # Qt's adjustSize() for top-level windows passes sizeHint().width() into
        # totalHeightForWidth().  That width varies with text content, so sparse
        # and rich metadata can accidentally produce the same total height.
        # QLabel.updateGeometry() also skips parent-layout invalidation when the
        # panel is hidden (isVisible() is False), leaving child layout caches
        # stale.  Use the actual widget width instead — totalHeightForWidth()
        # calls label.heightForWidth() directly, which is always fresh.
        w = max(self.width(), self.minimumWidth())
        if layout is not None and layout.hasHeightForWidth():
            h = layout.totalHeightForWidth(w)
            self.resize(w, h)
        else:
            self.adjustSize()
            target_size = self.sizeHint().expandedTo(self.minimumSize())
            if target_size.isValid():
                self.resize(target_size)
        if recenter:
            self._center_over_parent()

    def _center_over_parent(self) -> None:
        """Center the panel over its parent using the current widget size."""

        parent = self.parentWidget()
        if parent is None or not parent.isVisible():
            return
        center = parent.geometry().center()
        self.move(
            center.x() - self.width() // 2,
            center.y() - self.height() // 2,
        )

    def _schedule_post_show_reflow(self, *, recenter: bool) -> None:
        """Queue a deferred geometry reflow after show or visible content updates."""

        self._post_show_reflow_recenter = self._post_show_reflow_recenter or recenter
        if self._post_show_reflow_queued:
            return
        self._post_show_reflow_queued = True
        QTimer.singleShot(0, self._run_post_show_reflow)

    def _run_post_show_reflow(self) -> None:
        """Finalize the layout once the initial show event has fully settled."""

        self._post_show_reflow_queued = False
        recenter = self._post_show_reflow_recenter
        self._post_show_reflow_recenter = False
        self._refresh_panel_geometry(recenter=recenter)
        self.update()

    def _try_start_system_drag(self) -> bool:
        """Ask the window manager to move the frameless tool window if possible."""

        handle = self.windowHandle()
        if handle is None:
            return False
        try:
            return bool(handle.startSystemMove())
        except RuntimeError:
            return False

    def _begin_drag(self, event: QMouseEvent) -> bool:
        """Start a native or manual drag from a title-bar mouse press."""

        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._try_start_system_drag():
            self._drag_active = False
            self._drag_offset = None
            return True
        self._drag_active = True
        self._drag_offset = (
            event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        )
        return True

    def _continue_drag(self, event: QMouseEvent) -> bool:
        """Advance a manual drag if one is active."""

        if not self._drag_active:
            return False
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._drag_active = False
            self._drag_offset = None
            return True
        if self._drag_offset is None:
            return True
        target = event.globalPosition().toPoint() - self._drag_offset
        self.move(target)
        handle = self.windowHandle()
        if handle is not None:
            try:
                handle.setPosition(target)
            except RuntimeError:
                pass
        return True

    def _end_drag(self) -> bool:
        """Terminate a manual drag session."""

        if not self._drag_active:
            return False
        self._drag_active = False
        self._drag_offset = None
        return True

    # ------------------------------------------------------------------
    # QWidget overrides
    # ------------------------------------------------------------------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        is_title_target = watched in (self._title_bar, self._title_label)
        is_drag_event = event.type() in self._DRAG_EVENT_TYPES
        if is_title_target and is_drag_event:
            mouse_event = event  # type: ignore[assignment]
            if event.type() == QEvent.Type.MouseButtonPress and self._begin_drag(mouse_event):
                mouse_event.accept()
                return True
            if event.type() == QEvent.Type.MouseMove and self._continue_drag(mouse_event):
                mouse_event.accept()
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and self._end_drag():
                mouse_event.accept()
                return True
        if watched in (self._location_editor, self._location_results):
            if event.type() == QEvent.Type.ShortcutOverride:
                key_event = event  # type: ignore[assignment]
                if key_event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
                    key_event.accept()
                    return True
            if event.type() == QEvent.Type.KeyPress:
                key_event = event  # type: ignore[assignment]
                handled = (
                    self._handle_location_editor_key_press(key_event)
                    if watched is self._location_editor
                    else self._handle_location_results_key_press(key_event)
                )
                if handled:
                    key_event.accept()
                    return True
            if event.type() == QEvent.Type.KeyRelease:
                key_event = event  # type: ignore[assignment]
                if key_event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
                    key_event.accept()
                    return True
        return super().eventFilter(watched, event)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            self._apply_close_button_style()
            self._update_face_add_button_icon()
        super().changeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        """Centre the panel over its parent window the first time it appears."""

        super().showEvent(event)
        first_show = not self._centered
        if first_show:
            self._centered = True
        self._refresh_panel_geometry(recenter=first_show)
        self._schedule_post_show_reflow(recenter=first_show)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Emit a dismissal signal so the detail state stays in sync."""

        self.dismissed.emit()
        super().closeEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        """Draw drop shadow and an anti-aliased rounded rectangle."""

        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        s = self._SHADOW_SIZE
        content_rect = QRectF(self.rect()).adjusted(0, 0, -s, -s)
        radius = min(
            self._CORNER_RADIUS,
            min(content_rect.width(), content_rect.height()) / 2.0,
        )

        # -- drop shadow (right + bottom only) -----------------------------
        shadow_steps = s
        for i in range(shadow_steps):
            alpha = int(self._SHADOW_MAX_ALPHA * (1 - i / shadow_steps) ** 2)
            if alpha <= 0:
                continue
            shadow_color = QColor(0, 0, 0, alpha)
            spread = float(i)
            shadow_rect = content_rect.adjusted(spread, spread, spread, spread)
            shadow_path = QPainterPath()
            shadow_path.addRoundedRect(
                shadow_rect,
                radius + spread * self._SHADOW_RADIUS_GROWTH,
                radius + spread * self._SHADOW_RADIUS_GROWTH,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillPath(shadow_path, shadow_color)

        # -- background ----------------------------------------------------
        path = QPainterPath()
        path.addRoundedRect(content_rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        bg_color = self.palette().color(QPalette.ColorRole.Window)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, bg_color)

        border_color = self.palette().color(QPalette.ColorRole.Mid)
        border_color.setAlpha(80)
        painter.setPen(border_color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """Begin a drag when clicking on the title bar area."""

        if event.button() == Qt.MouseButton.LeftButton:
            local_pos = event.position().toPoint()
            if self._title_bar.geometry().contains(local_pos):
                if self._begin_drag(event):
                    event.accept()
                    return
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """Move the panel when dragging the title bar."""

        if self._continue_drag(event):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """End a title-bar drag."""

        if self._end_drag():
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _format_metadata(self, metadata: Mapping[str, Any]) -> _FormattedMetadata:
        """Return a :class:`_FormattedMetadata` snapshot for *metadata*."""

        info = dict(metadata)
        name = self._resolve_name(info)
        timestamp = self._format_timestamp(info.get("dt"))
        camera = self._format_camera(info)
        lens = self._format_lens(info)
        is_video = bool(info.get("is_video"))
        summary = (
            self._format_video_summary(info)
            if is_video
            else self._format_photo_summary(info)
        )
        exposure_line = (
            self._format_video_details(info)
            if is_video
            else self._format_exposure_line(info)
        )
        return _FormattedMetadata(
            name=name,
            timestamp=timestamp,
            camera=camera,
            lens=lens,
            summary=summary,
            exposure_line=exposure_line,
            is_video=is_video,
        )

    def _resolve_name(self, info: Mapping[str, Any]) -> str:
        """Return a human readable filename from *info*."""

        name = info.get("name")
        if isinstance(name, str) and name:
            return name
        rel = info.get("rel")
        if isinstance(rel, str) and rel:
            return Path(rel).name
        abs_path = info.get("abs")
        if isinstance(abs_path, str) and abs_path:
            return Path(abs_path).name
        return ""

    def _format_timestamp(self, value: Any) -> str:
        """Return *value* formatted using the current locale."""

        if not isinstance(value, str) or not value:
            return ""
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return ""
        localized = parsed.astimezone()
        qt_datetime = QDateTime(localized)
        formatted = QLocale.system().toString(qt_datetime, QLocale.FormatType.LongFormat)
        if formatted:
            return formatted
        return localized.strftime("%Y-%m-%d %H:%M:%S")

    def _format_camera(self, info: Mapping[str, Any]) -> str:
        """Combine camera make and model if they are available."""

        make = info.get("make") if isinstance(info.get("make"), str) else None
        model = info.get("model") if isinstance(info.get("model"), str) else None
        if make and model:
            if model.lower().startswith(make.lower()):
                return model
            return f"{make} {model}"
        if model:
            return model
        if make:
            return make
        return ""

    def _format_lens(self, info: Mapping[str, Any]) -> str:
        """Return the lens description augmented with focal and aperture data."""

        lens = info.get("lens") if isinstance(info.get("lens"), str) else None
        focal_text = self._format_focal_length(info.get("focal_length"))
        aperture_text = self._format_aperture(info.get("f_number"))
        components = [component for component in (focal_text, aperture_text) if component]
        # If the lens string already encodes both focal-length and aperture info
        # (e.g. a LensInfo spec string like "23mm f/2" or an iPhone model tag
        # "back camera 4.2mm f/1.6"), do not append the separate focal/aperture
        # fields — they would merely duplicate values already present in the
        # lens string.
        if lens and _LENS_SPEC_RE.search(lens):
            return lens
        if lens and components:
            return f"{lens} — {' '.join(components)}"
        if lens:
            return lens
        if components:
            return " ".join(components)
        return ""

    def _format_photo_summary(self, info: Mapping[str, Any]) -> str:
        """Compose a single line summarising the image dimensions and size."""

        width = info.get("w")
        height = info.get("h")
        dimensions = ""
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            dimensions = f"{width} × {height}"

        size_text = self._format_filesize(info.get("bytes"))
        format_text = self._format_format(info)
        parts = [part for part in (dimensions, size_text, format_text) if part]
        return "    ".join(parts)

    def _format_video_summary(self, info: Mapping[str, Any]) -> str:
        """Summarise a video's dimensions, size, and codec in a single line."""

        width = info.get("w")
        height = info.get("h")
        dimensions = ""
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            dimensions = f"{width} × {height}"

        size_text = self._format_filesize(info.get("bytes"))
        codec_text = self._format_codec(info)
        parts = [part for part in (dimensions, size_text, codec_text) if part]
        return "    ".join(parts)

    def _format_exposure_line(self, info: Mapping[str, Any]) -> str:
        """Compose the ISO, focal length, EV, aperture, and shutter speed line."""

        iso_value = info.get("iso")
        iso_text = ""
        if isinstance(iso_value, (int, float)):
            iso_text = f"ISO {int(round(float(iso_value)))}"

        focal_text = self._format_focal_length(info.get("focal_length"))
        ev_text = self._format_exposure_comp(info.get("exposure_compensation"))
        aperture_text = self._format_aperture(info.get("f_number"))
        shutter_text = self._format_shutter(info.get("exposure_time"))

        parts = [part for part in (iso_text, focal_text, ev_text, aperture_text, shutter_text) if part]
        return "    ".join(parts)

    def _format_video_details(self, info: Mapping[str, Any]) -> str:
        """Compose the frame-rate and duration line for a video asset."""

        frame_rate_text = self._format_frame_rate(info.get("frame_rate"))
        duration_text = self._format_duration(info.get("dur"))
        codec_summary = self._format_codec(info)
        codec_text = ""
        # Show the codec twice only when the summary had no value; this keeps
        # the layout tidy while still surfacing the information somewhere.
        if not codec_summary:
            codec_text = self._format_format(info)

        parts = [part for part in (frame_rate_text, duration_text, codec_text) if part]
        return "    ".join(parts)

    def _format_focal_length(self, value: Any) -> str:
        """Return a formatted focal length string in millimetres."""

        numeric = self._coerce_float(value)
        if numeric is None or numeric <= 0:
            return ""
        if abs(numeric - round(numeric)) < 0.05:
            return f"{int(round(numeric))} mm"
        return f"{numeric:.1f} mm"

    def _format_aperture(self, value: Any) -> str:
        """Return a formatted aperture string (ƒ-number)."""

        numeric = self._coerce_float(value)
        if numeric is None or numeric <= 0:
            return ""
        return f"ƒ{self._format_decimal(numeric, precision=2)}"

    def _format_exposure_comp(self, value: Any) -> str:
        """Return exposure compensation in EV when available."""

        numeric = self._coerce_float(value)
        if numeric is None:
            return ""
        text = self._format_decimal(numeric, precision=2)
        return f"{text} ev"

    def _format_shutter(self, value: Any) -> str:
        """Return shutter speed formatted as a fraction when suitable."""

        numeric = self._coerce_float(value)
        if numeric is None or numeric <= 0:
            return ""
        if numeric >= 1:
            return f"{self._format_decimal(numeric, precision=2)} s"
        fraction = Fraction(numeric).limit_denominator(8000)
        approx = fraction.numerator / fraction.denominator
        if abs(approx - numeric) <= 1e-4:
            if fraction.numerator == 1:
                return f"1/{fraction.denominator} s"
            return f"{fraction.numerator}/{fraction.denominator} s"
        return f"{self._format_decimal(numeric, precision=4)} s"

    def _format_filesize(self, value: Any) -> str:
        """Return *value* expressed in human readable units."""

        numeric = self._coerce_float(value)
        if numeric is None or numeric <= 0:
            return ""
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(numeric)
        unit_index = 0
        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1
        if unit_index == 0:
            return f"{int(size)} {units[unit_index]}"

        rounded = round(size, 1)
        if float(rounded).is_integer():
            return f"{int(rounded)} {units[unit_index]}"
        return f"{rounded:.1f} {units[unit_index]}"

    def _format_codec(self, info: Mapping[str, Any]) -> str:
        """Return a readable codec label derived from the stored metadata."""

        codec_value = info.get("codec")
        if isinstance(codec_value, str):
            candidate = codec_value.strip()
            if not candidate:
                return ""
            if "," in candidate:
                candidate = candidate.split(",", 1)[0].strip()
            if "/" in candidate:
                candidate = candidate.split("/")[-1].strip()
            if "(" in candidate:
                candidate = candidate.split("(")[0].strip()
            normalized = candidate.replace(".", "").replace("-", "").replace(" ", "").upper()
            mapping = {
                "H264": "H.264",
                "AVC": "H.264",
                "AVC1": "H.264",
                "H265": "H.265",
                "HEVC": "HEVC",
                "X265": "H.265",
                "PRORES": "ProRes",
            }
            if normalized in mapping:
                return mapping[normalized]
            if candidate.isupper():
                return candidate
            if candidate.islower():
                return candidate.upper()
            return candidate
        return self._format_format(info)

    def _format_frame_rate(self, value: Any) -> str:
        """Return the frame-rate with two decimal places when available."""

        numeric = self._coerce_float(value)
        if numeric is None or numeric <= 0:
            return ""
        return f"{self._format_decimal(numeric, precision=2)} fps"

    def _format_duration(self, value: Any) -> str:
        """Return a short ``mm:ss`` or ``hh:mm:ss`` string for *value* seconds."""

        numeric = self._coerce_float(value)
        if numeric is None or numeric < 0:
            return ""
        total_seconds = int(round(numeric))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:d}:{seconds:02d}"

    def _format_format(self, info: Mapping[str, Any]) -> str:
        """Return a short label describing the image format."""

        name = info.get("name") if isinstance(info.get("name"), str) else None
        if name:
            suffix = Path(name).suffix
            if suffix:
                extension = suffix.lstrip(".")
                if extension.lower() in {"heic", "heif"}:
                    return "HEIF"
                return extension.upper()
        mime = info.get("mime") if isinstance(info.get("mime"), str) else None
        if mime:
            subtype = mime.split("/")[-1]
            if subtype.lower() in {"heic", "heif"}:
                return "HEIF"
            return subtype.upper()
        return ""

    def _format_decimal(self, value: float, *, precision: int) -> str:
        """Return *value* formatted with ``precision`` decimal places."""

        text = f"{value:.{precision}f}"
        text = text.rstrip("0").rstrip(".")
        return text or "0"

    def _coerce_float(self, value: Any) -> Optional[float]:
        """Return *value* as ``float`` when it represents a numeric quantity."""

        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                return float(value)
            except ValueError:
                return None
        return None


def _avatar_pixmap(path: Path | None) -> QPixmap | None:
    if path is None or not path.exists():
        return None
    source = QPixmap(str(path))
    if source.isNull():
        return None
    size = _FACE_AVATAR_DIAMETER
    scaled = source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    rounded = QPixmap(size, size)
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0.0, 0.0, float(size), float(size)))
    painter.setClipPath(clip)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return rounded
