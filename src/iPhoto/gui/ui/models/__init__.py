"""Lazy exports for Qt models used by the GUI."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AlbumTreeModel": ("album_tree_model", "AlbumTreeModel"),
    "AlbumTreeRole": ("album_tree_model", "AlbumTreeRole"),
    "NodeType": ("album_tree_model", "NodeType"),
    "Roles": ("roles", "Roles"),
    "EditSession": ("edit_session", "EditSession"),
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
