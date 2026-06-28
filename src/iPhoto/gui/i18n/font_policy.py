"""Application font policy for translated Qt UI text."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

_LOGGER = logging.getLogger(__name__)
_SIMPLIFIED_CHINESE_LANGUAGE = "zh-CN"
_ORIGINAL_WIDGET_FONT_PROPERTY = "_iPhoto_original_language_font"
_WINDOWS_SIMPLIFIED_CHINESE_FONTS = (
    "Microsoft YaHei",
    "Microsoft Yahei",
    "微软雅黑",
)


@dataclass
class _FontState:
    app_id: int | None = None
    original_font: QFont | None = None
    applied_family: str | None = None


_STATE = _FontState()


def simplified_chinese_font_family(
    platform: str,
    available_families: Iterable[str],
) -> str | None:
    """Return the preferred Simplified Chinese UI font available on *platform*."""

    candidates = _font_candidates_for_platform(platform)
    if not candidates:
        return None

    available_by_name = {
        _normalise_family_name(family): family
        for family in available_families
        if family and not str(family).startswith("@")
    }
    for candidate in candidates:
        family = available_by_name.get(_normalise_family_name(candidate))
        if family:
            return family
    return None


def apply_language_font(effective_language: str) -> str | None:
    """Apply the app font matching *effective_language* and return its family."""

    app = QGuiApplication.instance()
    if app is None:
        return None

    _ensure_state_for_app(app)

    if effective_language != _SIMPLIFIED_CHINESE_LANGUAGE:
        _restore_windows_font(app)
        return None

    platform = sys.platform
    if platform != "win32":
        _restore_windows_font(app)
        return None

    return _apply_windows_simplified_chinese_font(app)


def language_font(base_font: QFont) -> QFont:
    """Return *base_font* with the active language font family, when required."""

    font = QFont(base_font)
    if _STATE.applied_family:
        font.setFamily(_STATE.applied_family)
    return font


def sync_widget_language_font(widget: QWidget) -> None:
    """Synchronise one existing widget with the active language font family."""

    if _STATE.applied_family:
        _apply_font_to_widget(widget, _STATE.applied_family)
    else:
        _restore_font_on_widget(widget)


def _apply_windows_simplified_chinese_font(app: QGuiApplication) -> str | None:
    family = simplified_chinese_font_family(sys.platform, _available_font_families())
    if family is None:
        _LOGGER.info("No Simplified Chinese UI font override available on %s", sys.platform)
        _restore_windows_font(app)
        return None

    if _STATE.original_font is None:
        _STATE.original_font = QFont(app.font())

    font = QFont(app.font())
    font.setFamily(family)
    app.setFont(font)
    _STATE.applied_family = family
    _sync_existing_widget_fonts(family)
    return family


def _font_candidates_for_platform(platform: str) -> tuple[str, ...]:
    if platform == "win32":
        return _WINDOWS_SIMPLIFIED_CHINESE_FONTS
    return ()


def _normalise_family_name(family: object) -> str:
    return str(family).strip().casefold()


def _available_font_families() -> list[str]:
    try:
        return list(QFontDatabase.families())
    except Exception:  # noqa: BLE001 - font probing must never block GUI startup.
        _LOGGER.warning("Unable to inspect Qt font database", exc_info=True)
        return []


def _ensure_state_for_app(app: QGuiApplication) -> None:
    app_id = id(app)
    if _STATE.app_id == app_id:
        return
    _STATE.app_id = app_id
    _STATE.original_font = None
    _STATE.applied_family = None


def _restore_windows_font(app: QGuiApplication) -> None:
    if _STATE.applied_family is None:
        return
    if _STATE.original_font is not None:
        app.setFont(QFont(_STATE.original_font))
    _restore_existing_widget_fonts()
    _STATE.original_font = None
    _STATE.applied_family = None


def _sync_existing_widget_fonts(family: str) -> None:
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.allWidgets():
        _apply_font_to_widget(widget, family)


def _apply_font_to_widget(widget: QWidget, family: str) -> None:
    if widget.property(_ORIGINAL_WIDGET_FONT_PROPERTY) is None:
        widget.setProperty(_ORIGINAL_WIDGET_FONT_PROPERTY, QFont(widget.font()))

    font = QFont(widget.font())
    if font.family() == family:
        return
    font.setFamily(family)
    widget.setFont(font)


def _restore_existing_widget_fonts() -> None:
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.allWidgets():
        _restore_font_on_widget(widget)


def _restore_font_on_widget(widget: QWidget) -> None:
    original = widget.property(_ORIGINAL_WIDGET_FONT_PROPERTY)
    if isinstance(original, QFont):
        widget.setFont(QFont(original))
    widget.setProperty(_ORIGINAL_WIDGET_FONT_PROPERTY, None)


__all__ = [
    "apply_language_font",
    "language_font",
    "simplified_chinese_font_family",
    "sync_widget_language_font",
]
