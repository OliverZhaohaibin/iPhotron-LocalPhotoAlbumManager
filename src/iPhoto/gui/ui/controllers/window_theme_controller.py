"""Manage window chrome theming and edit mode transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QPalette

from ..icon import load_icon
from ..ui_main_window import Ui_MainWindow
from ..widgets.collapsible_section import CollapsibleSection
from ..window_manager import RoundedWindowShell
from ..theme_manager import ThemeManager, ThemeColors, DARK_THEME
from ..palette import SIDEBAR_SELECTED_BACKGROUND, SIDEBAR_ICON_COLOR

if TYPE_CHECKING:
    from .detail_ui_controller import DetailUIController


class WindowThemeController(QObject):
    """Synchronise window chrome and widgets with the active theme."""

    def __init__(
        self,
        ui: Ui_MainWindow,
        window: QObject | None,
        theme_manager: ThemeManager,
    ) -> None:
        super().__init__(window)
        self._ui = ui
        self._window = window
        self._theme_manager = theme_manager
        self._detail_ui_controller: "DetailUIController" | None = None

        shell_parent = ui.window_shell.parentWidget()
        self._rounded_window_shell: RoundedWindowShell | None = (
            shell_parent if isinstance(shell_parent, RoundedWindowShell) else None
        )

        # Connect to theme changes
        self._theme_manager.themeChanged.connect(self._on_theme_changed)

        # Initial application
        self._apply_colors(self._theme_manager.current_colors())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_detail_ui_controller(
        self, controller: "DetailUIController" | None
    ) -> None:
        """Store *controller* so toolbar icon tinting follows theme changes."""
        self._detail_ui_controller = controller
        # Re-apply icon tints based on current theme
        self._update_icon_tints(self._theme_manager.current_colors())

    def apply_edit_theme(self) -> None:
        """Force the application into dark mode for editing."""
        self._theme_manager.set_force_dark(True)

    def restore_global_theme(self) -> None:
        """Restore the global theme (release edit mode override)."""
        self._theme_manager.set_force_dark(False)

    def get_shell_animation_colors(
        self, entering: bool
    ) -> tuple[RoundedWindowShell | None, QColor | None, QColor | None]:
        """Return the shell widget plus start/end colours for transition animations."""

        shell = self._rounded_window_shell
        if shell is None:
            return None, None, None

        # When entering edit mode: Start = Current Base (Light/Dark), End = Dark
        # When exiting edit mode: Start = Dark, End = Current Base

        base_colors = self._theme_manager.base_colors()
        base_bg = base_colors.window_background
        dark_bg = DARK_THEME.window_background

        if entering:
            return shell, base_bg, dark_bg
        return shell, dark_bg, base_bg

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _on_theme_changed(self, is_dark: bool) -> None:
        self._apply_colors(self._theme_manager.current_colors())

    def _apply_colors(self, colors: ThemeColors) -> None:
        """Apply the given *colors* to the window chrome and edit widgets."""

        # 1. Update general chrome stylesheets (Sidebar, Status Bar, Title Bar)
        # These widgets are transparent because the rounded shell handles the background.
        # But we need to set the text color.

        fg_color = colors.text_primary.name()
        disabled_fg = colors.text_disabled.name()
        outline_color = colors.border_color.name()

        # Update window title label color directly
        self._ui.window_title_label.setStyleSheet(f"color: {fg_color};")

        # Sidebar (Navigation)
        # Apply theme-aware background to ensure visual hierarchy (Light Blue in Light Mode, Gray in Dark Mode).
        sidebar_bg = colors.sidebar_background.name()

        # Match the window's rounded corners at the bottom-left
        radius = 0
        if self._rounded_window_shell:
            radius = self._rounded_window_shell.corner_radius()

        self._ui.sidebar.setStyleSheet(
            f"QWidget#albumSidebar {{ background-color: {sidebar_bg}; color: {fg_color}; border-bottom-left-radius: {radius}px; }}\n"
            f"QWidget#albumSidebar QLabel {{ color: {fg_color}; }}\n"
            f"QWidget#albumSidebar QTreeView {{ background-color: transparent; color: {fg_color}; }}\n"
            f"QWidget#albumSidebar QTreeView::item:selected {{ color: {colors.sidebar_text.name()}; }}"
        )
        # Apply specific palette for sidebar selection visualization
        sidebar_palette = self._ui.sidebar.palette()
        sidebar_palette.setColor(QPalette.ColorRole.Highlight, SIDEBAR_SELECTED_BACKGROUND)
        sidebar_palette.setColor(QPalette.ColorRole.HighlightedText, colors.sidebar_text)
        sidebar_palette.setColor(QPalette.ColorRole.Link, SIDEBAR_ICON_COLOR)
        self._ui.sidebar.setPalette(sidebar_palette)

        # Status Bar
        self._ui.status_bar.setStyleSheet(
            f"QWidget#chromeStatusBar {{ background-color: transparent; color: {fg_color}; }}\n"
            f"QWidget#chromeStatusBar QLabel {{ color: {fg_color}; }}"
        )

        # Window Chrome & Title Bar
        self._ui.window_chrome.setStyleSheet(f"background-color: transparent; color: {fg_color};")
        self._ui.title_bar.setStyleSheet(
            f"QWidget#windowTitleBar {{ background-color: transparent; color: {fg_color}; }}\n"
            f"QWidget#windowTitleBar QLabel {{ color: {fg_color}; }}\n"
            f"QWidget#windowTitleBar QToolButton {{ color: {fg_color}; }}"
        )
        self._ui.title_separator.setStyleSheet(
            f"QFrame#windowTitleSeparator {{ background-color: {outline_color}; border: none; }}"
        )

        # Menu Bar
        # Handled by WindowManager via _refresh_menu_styles, but we need to ensure the container is transparent
        self._ui.menu_bar_container.setStyleSheet(
            f"QWidget#menuBarContainer {{ background-color: transparent; color: {fg_color}; }}"
        )

        # Buttons (Rescan, Selection)
        for btn in (self._ui.rescan_button, self._ui.selection_button):
            name = btn.objectName()
            btn.setStyleSheet(
                f"QToolButton#{name} {{ background-color: transparent; color: {fg_color}; }}\n"
                f"QToolButton#{name}:disabled {{ background-color: transparent; color: {disabled_fg}; }}"
            )

        # Window Shell (holds the background)
        self._ui.window_shell.setAutoFillBackground(False)
        # We need to set the palette for the shell so it paints the background color
        shell_palette = self._ui.window_shell.palette()
        shell_palette.setColor(QPalette.ColorRole.Window, colors.window_background)
        self._ui.window_shell.setPalette(shell_palette)

        if self._rounded_window_shell:
            # Update the rounded shell's palette too, as WindowManager relies on it for menu styling
            rounded_palette = self._rounded_window_shell.palette()
            rounded_palette.setColor(QPalette.ColorRole.Window, colors.window_background)
            self._rounded_window_shell.setPalette(rounded_palette)
            self._rounded_window_shell.set_override_color(colors.window_background)

        # 2. Update Edit Container
        # The edit container always needs to look dark-ish, but if we are in Light Mode,
        # it is hidden. When in Edit Mode, force_dark is True, so `colors` IS Dark Theme.
        # So we can just apply `colors`.

        # However, the edit container has specific styling needs (rounded headers etc).
        # We construct the stylesheet based on `colors`.

        bg = colors.window_background.name()
        # EditThemeManager used #2C2C2E for header, which is lighter than #1C1C1E.
        # Our DARK_THEME.border_color is #323236 which is close.
        # Let's define some specific derived colors if needed, or rely on ThemeColors.

        # We'll use sidebar_background for panels
        panel_bg = colors.sidebar_background.name()

        edit_stylesheet = (
            f"QWidget#editPage {{ background-color: {bg}; }}\n"
            f"QWidget#editPage QLabel, QWidget#editPage QToolButton, QWidget#editHeaderContainer QPushButton {{ color: {fg_color}; }}\n"
            f"QWidget#editHeaderContainer {{ background-color: {panel_bg}; border-radius: 12px; }}\n"
            f"QWidget#editPage EditSidebar, QWidget#editPage EditSidebar QWidget, "
            f"QWidget#editPage QScrollArea, QWidget#editPage QScrollArea > QWidget {{ background-color: {panel_bg}; color: {fg_color}; }}\n"
            f"QWidget#editPage QGroupBox {{ background-color: {colors.window_background.darker(105).name()}; border: 1px solid {outline_color}; "
            "border-radius: 10px; margin-top: 24px; padding-top: 12px; }\n"
            f"QWidget#editPage QGroupBox::title {{ color: {fg_color}; subcontrol-origin: margin; left: 12px; padding: 0 4px; }}\n"
            f"QWidget#editPage #collapsibleSection QLabel {{ color: {fg_color}; }}"
        )
        self._ui.detail_page.edit_container.setStyleSheet(edit_stylesheet)

        # Detail/Edit View Background: Black in Dark Mode
        # Explicitly set the background color even for Light Mode to prevent sticky state
        target_surface = "#000000" if colors.is_dark else colors.window_background.name()
        self._ui.image_viewer.set_surface_color_override(target_surface)

        # 3. Update Icons and Buttons
        self._update_icon_tints(colors)

        # Update Edit Button style
        # It's in DetailPageWidget, so we need to construct it or assume logic matches
        edit_btn_bg = colors.window_background.name()
        edit_btn_hover = colors.window_background.darker(105).name()
        edit_btn_pressed = colors.window_background.darker(110).name()
        edit_btn_border = QColor(fg_color)
        edit_btn_border.setAlpha(30)

        self._ui.edit_button.setStyleSheet(
            "QPushButton {"
            f"  background-color: {edit_btn_bg};"
            f"  border: 1px solid {edit_btn_border.name(QColor.NameFormat.HexArgb)};"
            "  border-radius: 8px;"
            f"  color: {fg_color};"
            "  font-weight: 600;"
            "  padding-left: 20px;"
            "  padding-right: 20px;"
            "}"
            f"QPushButton:hover {{ background-color: {edit_btn_hover}; }}"
            f"QPushButton:pressed {{ background-color: {edit_btn_pressed}; }}"
            f"QPushButton:disabled {{ color: {disabled_fg}; border-color: {edit_btn_border.name(QColor.NameFormat.HexArgb)}; }}"
        )

        # 4. Refresh Menus
        self._refresh_menu_styles()

    def _update_icon_tints(self, colors: ThemeColors) -> None:
        """Update icon colors for buttons that need it."""
        icon_color = colors.text_primary.name(QColor.NameFormat.HexArgb)

        # Edit Sidebar Icons
        # We need to update CollapsibleSections
        sections = self._ui.edit_sidebar.findChildren(CollapsibleSection)
        for section in sections:
            section.set_toggle_icon_tint(colors.text_primary)
            icon_label = getattr(section, "_icon_label", None)
            icon_name = getattr(section, "_icon_name", "")
            if icon_label and icon_name:
                # Some icons have native colors
                if icon_name in {"color.circle.svg", "checkmark.svg"}:
                    icon_label.setPixmap(load_icon(icon_name).pixmap(20, 20))
                else:
                    icon_label.setPixmap(load_icon(icon_name, color=icon_color).pixmap(20, 20))

        self._ui.edit_sidebar.set_control_icon_tint(colors.text_primary)

        # Main/Edit Toolbar Icons
        # Zoom buttons
        self._ui.zoom_out_button.setIcon(load_icon("minus.svg", color=icon_color))
        self._ui.zoom_in_button.setIcon(load_icon("plus.svg", color=icon_color))

        # Back button
        self._ui.back_button.setIcon(load_icon("chevron.left.svg", color=icon_color))

        # Edit header buttons
        self._ui.edit_compare_button.setIcon(
            load_icon("square.fill.and.line.vertical.and.square.svg", color=icon_color)
        )

        # Detail Header buttons (Info, Favorite, Share, Rotate)
        if self._detail_ui_controller:
            self._detail_ui_controller.set_toolbar_icon_tint(colors.text_primary)
        else:
            self._ui.info_button.setIcon(load_icon("info.circle.svg", color=icon_color))
            self._ui.favorite_button.setIcon(load_icon("suit.heart.svg", color=icon_color))
            self._ui.share_button.setIcon(load_icon("square.and.arrow.up.svg", color=icon_color))
            self._ui.rotate_left_button.setIcon(load_icon("rotate.left.svg", color=icon_color))

    def _refresh_menu_styles(self) -> None:
        if self._window is None:
            return
        window_manager = getattr(self._window, "window_manager", None)
        if window_manager and hasattr(window_manager, "_apply_menu_styles"):
            window_manager._apply_menu_styles()
