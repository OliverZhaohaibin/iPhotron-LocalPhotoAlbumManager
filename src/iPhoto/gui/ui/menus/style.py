"""Shared styling helpers for popup menus."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QMenu, QWidget


def apply_menu_style(menu: QMenu, anchor: QWidget | None) -> None:
    """Apply the main window's rounded popup styling to ``menu``."""

    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    menu.setAutoFillBackground(True)
    menu.setWindowFlags(
        menu.windowFlags()
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.Popup
    )

    main_window = anchor.window() if anchor is not None else None
    if main_window is not None:
        menu.setPalette(main_window.palette())
        menu.setBackgroundRole(QPalette.ColorRole.Base)

        accessor = getattr(main_window, "get_qmenu_stylesheet", None)
        stylesheet: Optional[str]
        if callable(accessor):
            stylesheet = accessor()
        else:
            fallback_accessor = getattr(main_window, "menu_stylesheet", None)
            stylesheet = fallback_accessor() if callable(fallback_accessor) else None
        if isinstance(stylesheet, str) and stylesheet:
            menu.setStyleSheet(stylesheet)

    menu.setGraphicsEffect(None)
