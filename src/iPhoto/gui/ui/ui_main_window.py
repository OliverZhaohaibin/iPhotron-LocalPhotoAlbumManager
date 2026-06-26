"""UI definition for the primary application window."""

from __future__ import annotations

import os
import sys
import logging
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QMetaObject, QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMainWindow,
    QSizeGrip,
    QSizePolicy,
    QStackedLayout,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .icon import load_icon
from .widgets.album_sidebar import AlbumSidebar
from .widgets.chrome_status_bar import ChromeStatusBar
from .widgets.custom_title_bar import CustomTitleBar
from .widgets.gallery_page import GalleryPageWidget
from .widgets.main_header import MainHeaderWidget
from .widgets.notification_toast import NotificationToast

_LOGGER = logging.getLogger(__name__)


class FeatureKind(StrEnum):
    DETAIL = "detail"
    PREVIEW = "preview"
    MAP = "map"
    PEOPLE = "people"
    ALBUMS = "albums"


def _configure_opaque_widget_background(widget: QWidget, background: str | None = None) -> None:
    """Ensure container widgets provide an opaque backing fill."""

    if not widget.objectName():
        widget.setObjectName(type(widget).__name__)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
    widget.setAutoFillBackground(True)
    if background is not None:
        palette = QPalette(widget.palette())
        palette.setColor(QPalette.ColorRole.Window, QColor(background))
        widget.setPalette(palette)
        widget.setStyleSheet(
            f"QWidget#{widget.objectName()} {{ background-color: {background}; border: none; }}"
        )


def _configure_main_view_stack(view_stack: QStackedWidget, map_view: object) -> None:
    """Keep the native map page alive across view switches when possible."""

    stack_layout = view_stack.layout()
    if (
        isinstance(stack_layout, QStackedLayout)
        and hasattr(map_view, "uses_native_osmand_widget")
        and map_view.uses_native_osmand_widget()
        and sys.platform != "darwin"
        and os.environ.get("IPHOTO_KEEP_NATIVE_MAP_PAGE_ALIVE", "").strip().lower()
        in {"1", "true", "yes", "on"}
    ):
        # Keeping the native map page visible underneath the active page
        # avoids Qt tearing down the packaged QOpenGLWidget context every
        # time the user leaves the Location section. This is opt-in because
        # some packaged Qt builds emit QPainter/QGraphicsEffect warnings when
        # a hidden OpenGL page remains stacked beneath the active page.
        stack_layout.setStackingMode(QStackedLayout.StackAll)


class Ui_MainWindow(QObject):
    """Pure UI layer for :class:`~PySide6.QtWidgets.QMainWindow`."""

    featureCreated = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self._features: dict[FeatureKind, object] = {}
        self._main_window: QMainWindow | None = None
        self._library = None

    def setupUi(self, MainWindow: QMainWindow, library) -> None:  # noqa: N802 - Qt style
        """Instantiate only the widgets needed to paint the initial gallery."""

        self._main_window = MainWindow
        self._library = library

        if not MainWindow.objectName():
            MainWindow.setObjectName("MainWindow")

        MainWindow.resize(1200, 720)

        self.window_shell = QWidget(MainWindow)
        self.window_shell_layout = QVBoxLayout(self.window_shell)
        self.window_shell_layout.setContentsMargins(0, 0, 0, 0)
        self.window_shell_layout.setSpacing(0)

        self.resize_indicator = QLabel(MainWindow)
        self.resize_indicator.setObjectName("resizeIndicatorLabel")
        indicator_size = QSize(20, 20)
        self.resize_indicator.setFixedSize(indicator_size)
        self.resize_indicator.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.resize_indicator.setScaledContents(True)
        self.resize_indicator.setPixmap(load_icon("resize.svg").pixmap(indicator_size))
        self.resize_indicator.hide()

        self.size_grip = QSizeGrip(MainWindow)
        self.size_grip.setObjectName("resizeSizeGrip")
        self.size_grip.setFixedSize(indicator_size)
        self.size_grip.hide()

        self.window_chrome = QWidget(self.window_shell)
        self.window_chrome.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        window_chrome_layout = QVBoxLayout(self.window_chrome)
        window_chrome_layout.setContentsMargins(0, 0, 0, 0)
        window_chrome_layout.setSpacing(0)

        self.title_bar = CustomTitleBar(self.window_chrome, MainWindow.windowTitle())
        self.window_title_label = self.title_bar.window_title_label
        self.window_controls = self.title_bar.window_controls
        self.minimize_button = self.title_bar.minimize_button
        self.fullscreen_button = self.title_bar.fullscreen_button
        self.close_button = self.title_bar.close_button
        window_chrome_layout.addWidget(self.title_bar)

        self.title_separator = QFrame(self.window_chrome)
        self.title_separator.setObjectName("windowTitleSeparator")
        self.title_separator.setFrameShape(QFrame.Shape.HLine)
        self.title_separator.setFrameShadow(QFrame.Shadow.Plain)
        self.title_separator.setFixedHeight(1)
        window_chrome_layout.addWidget(self.title_separator)

        self.main_header = MainHeaderWidget(self.window_shell, MainWindow)
        self.menu_bar_container = self.main_header
        self.menu_bar = self.main_header.menu_bar
        self.rescan_button = self.main_header.rescan_button
        self.selection_button = self.main_header.selection_button
        self.open_album_action = self.main_header.open_album_action
        self.rescan_action = self.main_header.rescan_action
        self.rebuild_links_action = self.main_header.rebuild_links_action
        self.bind_library_action = self.main_header.bind_library_action
        self.download_map_extension_action = self.main_header.download_map_extension_action
        self.toggle_filmstrip_action = self.main_header.toggle_filmstrip_action
        self.toggle_face_names_action = self.main_header.toggle_face_names_action
        self.toggle_hidden_people_action = self.main_header.toggle_hidden_people_action
        self.export_all_edited_action = self.main_header.export_all_edited_action
        self.export_selected_action = self.main_header.export_selected_action
        self.export_destination_group = self.main_header.export_destination_group
        self.export_destination_library = self.main_header.export_destination_library
        self.export_destination_ask = self.main_header.export_destination_ask
        self.export_format_group = self.main_header.export_format_group
        self.export_format_jpg = self.main_header.export_format_jpg
        self.export_format_png = self.main_header.export_format_png
        self.export_format_tiff = self.main_header.export_format_tiff
        self.share_action_group = self.main_header.share_action_group
        self.share_action_copy_file = self.main_header.share_action_copy_file
        self.share_action_copy_path = self.main_header.share_action_copy_path
        self.share_action_reveal_file = self.main_header.share_action_reveal_file
        self.wheel_action_group = self.main_header.wheel_action_group
        self.wheel_action_navigate = self.main_header.wheel_action_navigate
        self.wheel_action_zoom = self.main_header.wheel_action_zoom
        self.theme_group = self.main_header.theme_group
        self.theme_system = self.main_header.theme_system
        self.theme_light = self.main_header.theme_light
        self.theme_dark = self.main_header.theme_dark
        self.language_group = self.main_header.language_group
        self.language_system = self.main_header.language_system
        self.language_de = self.main_header.language_de
        self.language_zh_cn = self.main_header.language_zh_cn

        self.window_shell_layout.addWidget(self.window_chrome)
        self.window_shell_layout.addWidget(self.menu_bar_container)

        self.sidebar = AlbumSidebar(library, MainWindow)
        self.gallery_page = GalleryPageWidget()
        self.grid_view = self.gallery_page.grid_view

        right_panel = QWidget()
        right_panel.setObjectName("rightPanel")
        _configure_opaque_widget_background(right_panel)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)

        self.view_stack = QStackedWidget()
        self.view_stack.setObjectName("mainViewStack")
        _configure_opaque_widget_background(self.view_stack)

        self.view_stack.addWidget(self.gallery_page)
        self.view_stack.setCurrentWidget(self.gallery_page)
        right_layout.addWidget(self.view_stack)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(right_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, True)
        self.splitter.setCollapsible(1, False)

        self.window_shell_layout.addWidget(self.splitter)

        self.status_bar = ChromeStatusBar(self.window_shell)
        self.window_shell_layout.addWidget(self.status_bar)
        self.progress_bar = self.status_bar.progress_bar

        MainWindow.setCentralWidget(self.window_shell)

        self.notification_toast = NotificationToast(MainWindow)

        self.retranslateUi(MainWindow)
        QMetaObject.connectSlotsByName(MainWindow)

    def ensure_feature(self, feature: FeatureKind | str) -> object:
        """Create and cache one hidden feature bundle on demand."""

        kind = FeatureKind(feature)
        existing = self._features.get(kind)
        if existing is not None:
            return existing
        factory = {
            FeatureKind.DETAIL: self._create_detail_feature,
            FeatureKind.PREVIEW: self._create_preview_feature,
            FeatureKind.MAP: self._create_map_feature,
            FeatureKind.PEOPLE: self._create_people_feature,
            FeatureKind.ALBUMS: self._create_albums_feature,
        }[kind]
        created = factory()
        self._features[kind] = created
        self.featureCreated.emit(kind.value, created)
        return created

    def _create_detail_feature(self) -> object:
        from .widgets.detail_page import DetailPageWidget
        from .widgets.gl_image_viewer import GLImageViewer
        from .widgets.info_panel import InfoPanel

        assert self._main_window is not None
        shared_image_viewer = GLImageViewer()
        self.detail_page = DetailPageWidget(self._main_window, image_viewer=shared_image_viewer)
        for name in (
            "back_button", "info_button", "share_button", "favorite_button",
            "rotate_left_button", "edit_button", "zoom_widget", "zoom_slider",
            "zoom_in_button", "zoom_out_button", "location_label", "timestamp_label",
            "detail_actions_layout", "detail_info_button_index", "detail_favorite_button_index",
            "detail_header_layout", "detail_zoom_widget_index", "detail_header",
            "detail_chrome_container", "detail_header_separator", "player_stack",
            "player_placeholder", "video_area", "player_bar", "video_trim_bar",
            "face_name_overlay", "filmstrip_view", "live_badge", "badge_host",
            "player_container", "edit_mode_group", "edit_adjust_action", "edit_crop_action",
            "edit_compare_button", "edit_reset_button", "edit_done_button",
            "edit_rotate_left_button", "edit_sidebar", "edit_mode_control",
            "edit_header_container", "edit_zoom_host", "edit_zoom_host_layout",
            "edit_right_controls_layout",
        ):
            setattr(self, name, getattr(self.detail_page, name))
        self.image_viewer = shared_image_viewer
        self.edit_image_viewer = shared_image_viewer
        self.view_stack.addWidget(self.detail_page)
        self.info_panel = InfoPanel(self._main_window)
        self.info_panel.set_map_runtime(getattr(self._library, "map_runtime", None))
        self.player_container.installEventFilter(self._main_window)
        self.retranslateUi(self._main_window)
        return self.detail_page

    def _create_preview_feature(self) -> object:
        from .widgets.preview_window import PreviewWindow

        assert self._main_window is not None
        self.preview_window = PreviewWindow(self._main_window)
        return self.preview_window

    def _create_map_feature(self) -> object:
        from maps.map_sources import apply_pending_osmand_extension_install
        from .widgets.photo_map_view import PhotoMapView

        assert self._main_window is not None
        maps_root = Path(__file__).resolve().parents[3] / "maps"
        try:
            apply_pending_osmand_extension_install(maps_root)
        except Exception:
            _LOGGER.warning("Failed to apply pending map extension install", exc_info=True)
        map_runtime = getattr(self._library, "map_runtime", None)
        refresh_runtime = getattr(map_runtime, "refresh", None)
        if callable(refresh_runtime):
            try:
                refresh_runtime()
            except Exception:
                _LOGGER.warning("Failed to refresh map runtime before map view creation", exc_info=True)
        self.map_view = PhotoMapView(
            map_runtime=map_runtime,
            map_interaction_service=getattr(self._library, "map_interaction_service", None),
        )
        self.map_page = QWidget()
        self.map_page.setObjectName("locationMapPage")
        _configure_opaque_widget_background(self.map_page, "#88a8c2")
        layout = QVBoxLayout(self.map_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.map_view)
        self.view_stack.addWidget(self.map_page)
        _configure_main_view_stack(self.view_stack, self.map_view)
        return self.map_page

    def _create_people_feature(self) -> object:
        from .widgets.people_dashboard import PeopleDashboardWidget

        self.people_page = PeopleDashboardWidget()
        self.view_stack.addWidget(self.people_page)
        return self.people_page

    def _create_albums_feature(self) -> object:
        from .widgets.albums_dashboard import AlbumsDashboard

        assert self._main_window is not None
        self.albums_dashboard_page = AlbumsDashboard(self._library, self._main_window)
        self.view_stack.addWidget(self.albums_dashboard_page)
        return self.albums_dashboard_page

    def retranslateUi(self, MainWindow: QMainWindow) -> None:  # noqa: N802 - Qt style
        """Apply translatable strings to the window."""

        self.main_header.retranslate_ui()
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", "iPhoto", None))
        self.window_title_label.setText(MainWindow.windowTitle())
        self.minimize_button.setToolTip(QCoreApplication.translate("MainWindow", "Minimize", None))
        self.fullscreen_button.setToolTip(
            QCoreApplication.translate("MainWindow", "Enter Full Screen", None)
        )
        self.close_button.setToolTip(QCoreApplication.translate("MainWindow", "Close", None))
        self.selection_button.setText(QCoreApplication.translate("MainWindow", "Select", None))
        self.selection_button.setToolTip(
            QCoreApplication.translate(
                "MainWindow",
                "Toggle multi-selection mode",
                None,
            )
        )
        if hasattr(self, "edit_adjust_action"):
            self.edit_adjust_action.setText(
                QCoreApplication.translate("MainWindow", "Adjust", None)
            )
            self.edit_crop_action.setText(QCoreApplication.translate("MainWindow", "Crop", None))
            self.edit_mode_control.setItems(
                (
                    self.edit_adjust_action.text(),
                    self.edit_crop_action.text(),
                )
            )
            self.edit_compare_button.setToolTip(
                QCoreApplication.translate(
                    "MainWindow",
                    "Press and hold to preview the unedited photo",
                    None,
                )
            )
            self.edit_reset_button.setText(
                QCoreApplication.translate("MainWindow", "Revert to Original", None)
            )
            self.edit_reset_button.setToolTip(
                QCoreApplication.translate(
                    "MainWindow",
                    "Restore every adjustment to its original value",
                    None,
                )
            )
            self.edit_done_button.setText(QCoreApplication.translate("MainWindow", "Done", None))
        self.open_album_action.setText(
            QCoreApplication.translate("MainWindow", "Open Album Folder…", None)
        )
        self.rescan_action.setText(QCoreApplication.translate("MainWindow", "Rescan", None))
        self.rebuild_links_action.setText(
            QCoreApplication.translate("MainWindow", "Rebuild Live Links", None)
        )
        self.bind_library_action.setText(
            QCoreApplication.translate("MainWindow", "Set Basic Library…", None)
        )
        self.download_map_extension_action.setText(
            QCoreApplication.translate("MainWindow", "Download Map Extension…", None)
        )
        self.toggle_filmstrip_action.setText(
            QCoreApplication.translate("MainWindow", "Show Filmstrip", None)
        )
        self.share_action_copy_file.setText(
            QCoreApplication.translate("MainWindow", "Copy File", None)
        )
        self.share_action_copy_path.setText(
            QCoreApplication.translate("MainWindow", "Copy Path", None)
        )
        self.share_action_reveal_file.setText(
            QCoreApplication.translate("MainWindow", "Reveal in File Manager", None)
        )
        self.wheel_action_navigate.setText(
            QCoreApplication.translate("MainWindow", "Navigate", None)
        )
        self.wheel_action_zoom.setText(QCoreApplication.translate("MainWindow", "Zoom", None))


__all__ = ["FeatureKind", "Ui_MainWindow"]
