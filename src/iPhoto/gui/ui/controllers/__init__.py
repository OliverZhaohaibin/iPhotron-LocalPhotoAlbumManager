"""Lazy controller exports; importing one controller must not load the GUI graph."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ContextMenuController": ("context_menu_controller", "ContextMenuController"),
    "DialogController": ("dialog_controller", "DialogController"),
    "HeaderController": ("header_controller", "HeaderController"),
    "WindowThemeController": ("window_theme_controller", "WindowThemeController"),
    "PlayerViewController": ("player_view_controller", "PlayerViewController"),
    "EditViewTransitionManager": ("edit_view_transition", "EditViewTransitionManager"),
    "PreviewController": ("preview_controller", "PreviewController"),
    "SelectionController": ("selection_controller", "SelectionController"),
    "ShareController": ("share_controller", "ShareController"),
    "StatusBarController": ("status_bar_controller", "StatusBarController"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value
