"""Header row containing the menu bar and primary toolbar buttons."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QAction, QActionGroup, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMenu,
    QMenuBar,
    QSizePolicy,
    QSpacerItem,
    QToolButton,
    QWidget,
)


class MainHeaderWidget(QWidget):
    """Container hosting the menu bar alongside quick access buttons."""

    def __init__(self, parent: QWidget | None, main_window: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("menuBarContainer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.menu_bar = QMenuBar(self)
        self.menu_bar.setObjectName("chromeMenuBar")
        self.menu_bar.setNativeMenuBar(False)
        self.menu_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.menu_bar.setAutoFillBackground(True)
        self.menu_bar.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        layout.addWidget(self.menu_bar)
        layout.addSpacerItem(
            QSpacerItem(
                1,
                1,
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Minimum,
            )
        )

        self.rescan_button = QToolButton(self)
        self.rescan_button.setObjectName("rescanButton")
        self.rescan_button.setAutoRaise(True)
        layout.addWidget(self.rescan_button)

        self.selection_button = QToolButton(self)
        self.selection_button.setObjectName("selectionButton")
        self.selection_button.setAutoRaise(True)
        self.selection_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        layout.addWidget(self.selection_button)

        self._synchronise_palettes()
        self._create_actions(main_window)
        self._populate_menus()

    def _synchronise_palettes(self) -> None:
        """Ensure the container and menu bar share the same opaque background."""

        menu_palette = self.menu_bar.palette()
        menu_palette.setColor(
            QPalette.ColorRole.Window,
            menu_palette.color(QPalette.ColorRole.Base),
        )
        self.menu_bar.setPalette(menu_palette)

        container_palette = self.palette()
        container_palette.setColor(
            QPalette.ColorRole.Window,
            menu_palette.color(QPalette.ColorRole.Base),
        )
        self.setPalette(container_palette)

    def _create_actions(self, main_window: QWidget) -> None:
        """Instantiate the :class:`QAction` objects exposed to controllers."""

        self.open_album_action = QAction("", main_window)
        self.rescan_action = QAction("", main_window)
        self.rebuild_links_action = QAction("", main_window)
        self.bind_library_action = QAction("", main_window)
        self.download_map_extension_action = QAction("", main_window)
        self.toggle_filmstrip_action = QAction("", main_window, checkable=True)
        self.toggle_filmstrip_action.setChecked(True)
        self.toggle_face_names_action = QAction("", main_window, checkable=True)
        self.toggle_face_names_action.setChecked(False)
        self.toggle_hidden_people_action = QAction(
            "", main_window, checkable=True
        )
        self.toggle_hidden_people_action.setChecked(False)

        self.share_action_group = QActionGroup(main_window)
        self.share_action_copy_file = QAction("", main_window, checkable=True)
        self.share_action_copy_path = QAction("", main_window, checkable=True)
        self.share_action_reveal_file = QAction(
            "", main_window, checkable=True
        )
        self.share_action_group.addAction(self.share_action_copy_file)
        self.share_action_group.addAction(self.share_action_copy_path)
        self.share_action_group.addAction(self.share_action_reveal_file)
        self.share_action_reveal_file.setChecked(True)

        self.wheel_action_group = QActionGroup(main_window)
        self.wheel_action_navigate = QAction("", main_window, checkable=True)
        self.wheel_action_zoom = QAction("", main_window, checkable=True)
        self.wheel_action_group.addAction(self.wheel_action_navigate)
        self.wheel_action_group.addAction(self.wheel_action_zoom)
        self.wheel_action_navigate.setChecked(True)

        self.export_all_edited_action = QAction("", main_window)
        self.export_selected_action = QAction("", main_window)

        self.export_destination_group = QActionGroup(main_window)
        self.export_destination_library = QAction("", main_window, checkable=True)
        self.export_destination_ask = QAction("", main_window, checkable=True)
        self.export_destination_group.addAction(self.export_destination_library)
        self.export_destination_group.addAction(self.export_destination_ask)
        self.export_destination_library.setChecked(True)

        self.export_format_group = QActionGroup(main_window)
        self.export_format_jpg = QAction("JPG", main_window, checkable=True)
        self.export_format_png = QAction("PNG", main_window, checkable=True)
        self.export_format_tiff = QAction("TIFF", main_window, checkable=True)
        self.export_format_group.addAction(self.export_format_jpg)
        self.export_format_group.addAction(self.export_format_png)
        self.export_format_group.addAction(self.export_format_tiff)
        self.export_format_jpg.setChecked(True)

        self.theme_group = QActionGroup(main_window)
        self.theme_system = QAction("", main_window, checkable=True)
        self.theme_light = QAction("", main_window, checkable=True)
        self.theme_dark = QAction("", main_window, checkable=True)
        self.theme_group.addAction(self.theme_system)
        self.theme_group.addAction(self.theme_light)
        self.theme_group.addAction(self.theme_dark)
        self.theme_system.setChecked(True)

        self.language_group = QActionGroup(main_window)
        self.language_group.setExclusive(True)
        self.language_system = QAction("", main_window, checkable=True)
        self.language_de = QAction("Deutsch", main_window, checkable=True)
        self.language_zh_cn = QAction("简体中文", main_window, checkable=True)
        self.language_system.setData("system")
        self.language_de.setData("de")
        self.language_zh_cn.setData("zh-CN")
        self.language_group.addAction(self.language_system)
        self.language_group.addAction(self.language_de)
        self.language_group.addAction(self.language_zh_cn)
        self.language_system.setChecked(True)

    def _populate_menus(self) -> None:
        """Populate the menu bar and wire shared actions to widgets."""

        self.file_menu = self._add_menu()
        for action in (
            self.open_album_action,
            None,
            self.bind_library_action,
            None,
            self.export_all_edited_action,
            self.export_selected_action,
            None,
            self.rebuild_links_action,
        ):
            if action is None:
                self.file_menu.addSeparator()
            else:
                self.file_menu.addAction(action)

        self.rescan_button.setDefaultAction(self.rescan_action)

        self.view_menu = self._add_menu()
        self.view_menu.addAction(self.toggle_face_names_action)
        self.view_menu.addAction(self.toggle_hidden_people_action)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.toggle_filmstrip_action)

        self.settings_menu = self._add_menu()
        self.settings_menu.addAction(self.bind_library_action)
        self.settings_menu.addAction(self.download_map_extension_action)
        self.settings_menu.addSeparator()

        self.appearance_menu = self._add_submenu(self.settings_menu)
        self.appearance_menu.addAction(self.theme_system)
        self.appearance_menu.addAction(self.theme_light)
        self.appearance_menu.addAction(self.theme_dark)

        self.language_menu = self._add_submenu(self.settings_menu)
        self.language_menu.addAction(self.language_system)
        self.language_menu.addAction(self.language_de)
        self.language_menu.addAction(self.language_zh_cn)

        self.export_dest_menu = self._add_submenu(self.settings_menu)
        self.export_dest_menu.addAction(self.export_destination_library)
        self.export_dest_menu.addAction(self.export_destination_ask)

        self.export_fmt_menu = self._add_submenu(self.settings_menu)
        self.export_fmt_menu.addAction(self.export_format_jpg)
        self.export_fmt_menu.addAction(self.export_format_png)
        self.export_fmt_menu.addAction(self.export_format_tiff)

        self.wheel_menu = self._add_submenu(self.settings_menu)
        self.wheel_menu.addAction(self.wheel_action_navigate)
        self.wheel_menu.addAction(self.wheel_action_zoom)

        self.share_menu = self._add_submenu(self.settings_menu)
        self.share_menu.addAction(self.share_action_copy_file)
        self.share_menu.addAction(self.share_action_copy_path)
        self.share_menu.addAction(self.share_action_reveal_file)
        self.retranslate_ui()

    def _add_menu(self) -> QMenu:
        menu = QMenu(self.menu_bar)
        self.menu_bar.addMenu(menu)
        return menu

    def _add_submenu(self, parent: QMenu) -> QMenu:
        menu = QMenu(parent)
        parent.addMenu(menu)
        return menu

    def retranslate_ui(self) -> None:
        """Refresh menu and action labels after a language change."""

        tr = QCoreApplication.translate
        self.file_menu.setTitle(tr("MainHeader", "&File", None))
        self.view_menu.setTitle(tr("MainHeader", "&View", None))
        self.settings_menu.setTitle(tr("MainHeader", "&Settings", None))
        self.appearance_menu.setTitle(tr("MainHeader", "Appearance", None))
        self.language_menu.setTitle(tr("MainHeader", "Language", None))
        self.export_dest_menu.setTitle(tr("MainHeader", "Export Destination", None))
        self.export_fmt_menu.setTitle(tr("MainHeader", "Export Format", None))
        self.wheel_menu.setTitle(tr("MainHeader", "Wheel Action", None))
        self.share_menu.setTitle(tr("MainHeader", "Share Action", None))

        self.open_album_action.setText(tr("MainWindow", "Open Album Folder…", None))
        self.rescan_action.setText(tr("MainWindow", "Rescan", None))
        self.rebuild_links_action.setText(tr("MainWindow", "Rebuild Live Links", None))
        self.bind_library_action.setText(tr("MainWindow", "Set Basic Library…", None))
        self.download_map_extension_action.setText(
            tr("MainWindow", "Download Map Extension…", None)
        )
        self.toggle_filmstrip_action.setText(tr("MainWindow", "Show Filmstrip", None))
        self.toggle_face_names_action.setText(tr("MainHeader", "Show face names", None))
        self.toggle_hidden_people_action.setText(tr("MainHeader", "Show Hidden People", None))

        self.export_all_edited_action.setText(tr("MainHeader", "Export All Edited", None))
        self.export_selected_action.setText(tr("MainHeader", "Export Selected", None))
        self.export_destination_library.setText(tr("MainHeader", "Basic Library", None))
        self.export_destination_ask.setText(tr("MainHeader", "Ask Every Time", None))
        self.export_format_jpg.setText(tr("MainHeader", "JPG", None))
        self.export_format_png.setText(tr("MainHeader", "PNG", None))
        self.export_format_tiff.setText(tr("MainHeader", "TIFF", None))

        self.theme_system.setText(tr("MainHeader", "System Default", None))
        self.theme_light.setText(tr("MainHeader", "Light Mode", None))
        self.theme_dark.setText(tr("MainHeader", "Dark Mode", None))

        self.language_system.setText(tr("MainHeader", "English", None))
        self.language_de.setText("Deutsch")
        self.language_zh_cn.setText("简体中文")

        self.share_action_copy_file.setText(tr("MainWindow", "Copy File", None))
        self.share_action_copy_path.setText(tr("MainWindow", "Copy Path", None))
        self.share_action_reveal_file.setText(tr("MainWindow", "Reveal in File Manager", None))
        self.wheel_action_navigate.setText(tr("MainWindow", "Navigate", None))
        self.wheel_action_zoom.setText(tr("MainWindow", "Zoom", None))


__all__ = ["MainHeaderWidget"]
