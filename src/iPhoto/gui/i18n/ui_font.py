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
_FALLBACKS_CONFIGURED = False


def apply_language_font(effective_language: str) -> None:
    """Apply the Windows app font required by *effective_language*.

    Windows exposes Microsoft YaHei under different family spellings depending
    on locale and Qt/font backend.  Linux font support is configured once at
    startup through fallback families so language switching does not rebuild
    visible top-level windows.
    """

    if sys.platform != "win32":
        return

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


def configure_application_font_fallbacks() -> None:
    """Configure platform font fallbacks that must be stable before widgets exist."""

    global _FALLBACKS_CONFIGURED

    if not sys.platform.startswith("linux"):
        return

    app = QApplication.instance()
    if app is None:
        return

    if _FALLBACKS_CONFIGURED:
        return
    _FALLBACKS_CONFIGURED = True

    family = _resolve_linux_simplified_chinese_fallback_family()
    if family is None:
        return

    current_font = app.font()
    current_families = _font_families(current_font)
    if any(candidate.casefold() == family.casefold() for candidate in current_families):
        return

    target_font = QFont(current_font)
    target_font.setFamilies([*current_families, family])
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
    else:
        return None

    available = {family.casefold(): family for family in QFontDatabase.families()}
    for candidate in candidates:
        family = available.get(candidate.casefold())
        if family is not None:
            return family
    return None


def _resolve_linux_simplified_chinese_fallback_family() -> str | None:
    available = _available_font_families_for_simplified_chinese()
    for candidate in _LINUX_SIMPLIFIED_CHINESE_FONT_CANDIDATES:
        family = available.get(candidate.casefold())
        if family is not None:
            return family
    return None


def _available_font_families_for_simplified_chinese() -> dict[str, str]:
    writing_system = QFontDatabase.WritingSystem.SimplifiedChinese
    return {family.casefold(): family for family in QFontDatabase.families(writing_system)}


def _font_families(font: QFont) -> list[str]:
    families = [family for family in font.families() if family]
    if families:
        return families
    family = font.family()
    return [family] if family else []


__all__ = ["apply_language_font", "configure_application_font_fallbacks"]
