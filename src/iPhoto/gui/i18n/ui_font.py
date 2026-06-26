"""Application font policy for translated Qt UI."""

from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

_WINDOWS_SIMPLIFIED_CHINESE_FONT_CANDIDATES = (
    "Microsoft YaHei",
    "Microsoft Yahei",
    "Microsoft YaHei UI",
    "Microsoft Yahei UI",
    "微软雅黑",
)
_LINUX_SIMPLIFIED_CHINESE_FONT_CANDIDATES = ("Noto Sans CJK SC",)
_ORIGINAL_APP_FONT: QFont | None = None


def apply_language_font(effective_language: str) -> None:
    """Apply the app font required by *effective_language*.

    Windows exposes Microsoft YaHei under different family spellings depending
    on locale and Qt/font backend.  Linux gets a best-effort Noto Sans CJK SC
    override when available.  Resolve the installed family before setting it so
    Qt receives the exact platform name.
    """

    app = QApplication.instance()
    if app is None:
        return

    _remember_original_font(app.font())
    if effective_language != "zh-CN":
        _restore_original_font(app)
        return

    family = _resolve_simplified_chinese_font_family()
    if family is None:
        return

    target_font = QFont(_ORIGINAL_APP_FONT or app.font())
    target_font.setFamily(family)
    app.setFont(target_font)


def _remember_original_font(font: QFont) -> None:
    global _ORIGINAL_APP_FONT

    if _ORIGINAL_APP_FONT is None:
        _ORIGINAL_APP_FONT = QFont(font)


def _restore_original_font(app: QApplication) -> None:
    if _ORIGINAL_APP_FONT is not None and app.font().family() != _ORIGINAL_APP_FONT.family():
        app.setFont(QFont(_ORIGINAL_APP_FONT))


def _resolve_simplified_chinese_font_family() -> str | None:
    if sys.platform == "win32":
        candidates = _WINDOWS_SIMPLIFIED_CHINESE_FONT_CANDIDATES
    elif sys.platform.startswith("linux"):
        candidates = _LINUX_SIMPLIFIED_CHINESE_FONT_CANDIDATES
    else:
        return None

    available = {family.casefold(): family for family in QFontDatabase.families()}
    for candidate in candidates:
        family = available.get(candidate.casefold())
        if family is not None:
            return family
    return None


__all__ = ["apply_language_font"]
