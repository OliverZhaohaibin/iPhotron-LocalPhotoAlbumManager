"""Tests for album sidebar menu dialog styling.

These tests verify that the palette constants used for styling album
dialogs are correctly defined to ensure a light theme appearance.
"""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for menu tests", exc_type=ImportError)

# Import palette module directly without triggering package __init__.py
palette_path = Path(__file__).parent.parent / "src" / "iPhoto" / "gui" / "ui" / "palette.py"
spec = importlib.util.spec_from_file_location("palette", palette_path)
palette = importlib.util.module_from_spec(spec)
spec.loader.exec_module(palette)


def test_sidebar_background_color_is_light_gray() -> None:
    """Verify that SIDEBAR_BACKGROUND_COLOR is the expected light gray."""
    background_color = palette.SIDEBAR_BACKGROUND_COLOR.name()
    
    # Ensure background is the light gray color specified in requirements
    assert background_color == "#eef3f6"
    # Ensure it's not black (the bug we're fixing)
    assert background_color != "#000000"


def test_sidebar_text_color_is_dark_gray() -> None:
    """Verify that SIDEBAR_TEXT_COLOR is the expected dark gray for contrast."""
    text_color = palette.SIDEBAR_TEXT_COLOR.name()
    
    # Ensure text is the dark gray color specified in requirements
    assert text_color == "#2b2b2b"


def test_palette_colors_provide_sufficient_contrast() -> None:
    """Verify that background and text colors have sufficient contrast."""
    background_color = palette.SIDEBAR_BACKGROUND_COLOR.name()
    text_color = palette.SIDEBAR_TEXT_COLOR.name()
    
    # Light background (#eef3f6) with dark text (#2b2b2b) provides good contrast
    # This is a simple sanity check that they're different
    assert background_color != text_color
    
    # Background should be lighter (higher hex values)
    # Text should be darker (lower hex values)
    bg_value = int(background_color[1:3], 16)  # Red component of background
    text_value = int(text_color[1:3], 16)  # Red component of text
    assert bg_value > text_value, "Background should be lighter than text"
