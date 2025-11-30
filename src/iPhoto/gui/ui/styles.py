"""Reusable style generators for the UI."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette


def modern_scrollbar_style(
    base_color: QColor,
    *,
    track_alpha: int = 30,
    handle_alpha: int = 40,
    handle_hover_alpha: int = 100,
    radius: int = 4,
    handle_radius: int = 3,
    extra_selectors: str = "",
) -> str:
    """Generate a CSS string for a modern, transparent scrollbar.

    Parameters
    ----------
    base_color:
        The reference color (usually text color) used to derive the handle and track colors.
        It should generally be fully opaque; alpha will be adjusted by this function.
    track_alpha:
        The alpha value (0-255) for the scrollbar track background. Defaults to 0 (transparent).
    handle_alpha:
        The alpha value (0-255) for the scrollbar handle.
    handle_hover_alpha:
        The alpha value (0-255) for the scrollbar handle when hovered.
    radius:
        Border radius for the scrollbar track/widget.
    handle_radius:
        Border radius for the scrollbar handle.
    extra_selectors:
        A comma-separated string of additional selectors to prepend to the generic
        QScrollBar selectors (e.g., specific widget IDs or parent classes).
    """

    if base_color.alpha() < 255:
        base_color = QColor(base_color)
        base_color.setAlpha(255)

    track_color = QColor(base_color)
    track_color.setAlpha(track_alpha)
    handle_color = QColor(base_color)
    handle_color.setAlpha(handle_alpha)
    handle_hover_color = QColor(base_color)
    handle_hover_color.setAlpha(handle_hover_alpha)

    track_hex = track_color.name(QColor.NameFormat.HexArgb)
    handle_hex = handle_color.name(QColor.NameFormat.HexArgb)
    handle_hover_hex = handle_hover_color.name(QColor.NameFormat.HexArgb)

    # Build the main selector list
    selectors = [
        "QScrollBar:vertical",
        "QScrollBar:horizontal",
    ]

    # Add context-aware selectors if provided (for global stylesheets)
    # The caller can pass "QWidget QScrollBar:vertical, #myWidget QScrollBar:vertical"
    # But usually, if applying to a specific widget, the generic selector is enough.
    # However, window_manager.py applies to the app, so it needs many selectors.

    # To support the complex selector list from window_manager.py, we can handle it
    # slightly differently or just let the caller prepend/replace.
    # Actually, the window_manager one had separate blocks for vertical/horizontal
    # combined with many parents.

    # Let's define the body logic first.

    # For reuse, we return the body mostly. But window_manager used specific selectors.

    # Let's construct a flexible string.

    # Common base style for both orientations
    base_css = (
        f"    background-color: {track_hex};\n"
        "    margin: 0px;\n"
        "    padding: 0px;\n"
        "    border: none;\n"
        f"    border-radius: {radius}px;\n"
    )

    handle_css = (
        f"    background-color: {handle_hex};\n"
        f"    border-radius: {handle_radius}px;\n"
        "    margin: 1px;\n"
    )

    handle_hover_css = (
        f"    background-color: {handle_hover_hex};\n"
    )

    # We can't easily merge the complex selector list from window_manager with simple ones.
    # But we can assume the caller wants standard QScrollBar styling.

    if extra_selectors:
        # If extra selectors are provided, we assume they are for the "base" scrollbar element.
        # We need to split them into vertical/horizontal or just apply to both if generic?
        # window_manager distinguished vertical/horizontal mainly for width/height.
        pass

    # Simplified approach: Return a formatted string with placeholders or just standard QScrollBar
    # If the caller needs specific selectors, they can use string replacement or we provide a mode.

    # Actually, window_manager used:
    # "QScrollBar:vertical, QWidget QScrollBar:vertical, ... { ... }"

    # Let's construct the full standard block.

    css = (
        f"/* Modern Scrollbar Style */\n"
        f"QScrollBar:vertical, QScrollBar:horizontal {extra_selectors} {{\n"
        f"{base_css}"
        "}\n"
        f"QScrollBar:vertical {extra_selectors} {{\n"
        "    width: 7px;\n"
        "}\n"
        f"QScrollBar:horizontal {extra_selectors} {{\n"
        "    height: 7px;\n"
        "}\n"
        f"QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{\n"
        f"{handle_css}"
        "}\n"
        "QScrollBar::handle:vertical {\n"
        "    min-height: 30px;\n"
        "}\n"
        "QScrollBar::handle:horizontal {\n"
        "    min-width: 30px;\n"
        "}\n"
        f"QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{\n"
        f"{handle_hover_css}"
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

    return css
