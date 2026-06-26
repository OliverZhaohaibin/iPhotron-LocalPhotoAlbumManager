"""Qt translation runtime service for the desktop GUI."""

from __future__ import annotations

import json
import logging
from importlib import resources
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication, QLocale, QObject, QTranslator, Signal

from ...settings import SettingsManager
from . import formatters
from .language import LanguageInfo
from .ui_font import apply_language_font

_LOGGER = logging.getLogger(__name__)
_RESOURCE_PACKAGE = "iPhoto.resources.i18n"
_DEFAULT_LANGUAGE = "en"
_INSTALLED_TRANSLATOR: QTranslator | None = None


class TranslationManager(QObject):
    """Install and switch Qt translators based on user settings."""

    languageChanged = Signal(str)  # noqa: N815 - Qt signal names use mixedCase.

    def __init__(self, settings: SettingsManager) -> None:
        super().__init__()
        self._settings = settings
        self._translator: QTranslator | None = None
        self._available = self._load_languages()
        self._current_language = str(settings.get("ui.language", "system") or "system")
        self._effective_language = _DEFAULT_LANGUAGE
        self._settings.settingsChanged.connect(self._on_settings_changed)

    def current_language(self) -> str:
        """Return the persisted language preference."""

        return self._current_language

    def effective_language(self) -> str:
        """Return the language currently installed, or ``en`` for fallback."""

        return self._effective_language

    def available_languages(self) -> list[LanguageInfo]:
        """Return every language advertised by the bundled metadata."""

        return list(self._available.values())

    def set_language(self, language: str) -> None:
        """Persist and apply a language preference."""

        normalized = self._normalize_language(language)
        if self._settings.get("ui.language", "system") == normalized:
            self.apply_language(normalized)
            return
        self._settings.set("ui.language", normalized)

    def apply_language(self, language: str | None = None) -> None:
        """Apply *language* or the current persisted setting."""

        requested = self._normalize_language(
            language if language is not None else self._settings.get("ui.language", "system")
        )
        effective = self._resolve_effective_language(requested)

        app = QCoreApplication.instance()
        if app is None:
            self._current_language = requested
            self._effective_language = effective
            self._apply_formatter_locale(effective)
            return

        self._remove_installed_translator(app)

        if effective != _DEFAULT_LANGUAGE:
            info = self._available.get(effective)
            loaded = False
            if info is not None and info.qm:
                translator = QTranslator()
                loaded = self._load_translator(translator, info.qm)
                if loaded:
                    app.installTranslator(translator)
                    self._translator = translator
                    self._set_installed_translator(translator)
                else:
                    _LOGGER.warning(
                        "Unable to load translation resource for %s; falling back to English",
                        effective,
                    )
            if not loaded:
                effective = _DEFAULT_LANGUAGE

        self._apply_formatter_locale(effective)
        apply_language_font(effective)

        changed = requested != self._current_language or effective != self._effective_language
        self._current_language = requested
        self._effective_language = effective
        if changed:
            self.languageChanged.emit(effective)

    def _on_settings_changed(self, key: str, value: object) -> None:
        if key == "ui.language":
            self.apply_language(str(value or "system"))

    def _remove_installed_translator(self, app: QCoreApplication) -> None:
        global _INSTALLED_TRANSLATOR

        if _INSTALLED_TRANSLATOR is not None:
            try:
                app.removeTranslator(_INSTALLED_TRANSLATOR)
            except RuntimeError:
                pass
        _INSTALLED_TRANSLATOR = None
        self._translator = None

    def _set_installed_translator(self, translator: QTranslator) -> None:
        global _INSTALLED_TRANSLATOR

        _INSTALLED_TRANSLATOR = translator

    def _normalize_language(self, language: object) -> str:
        code = str(language or "system")
        if code == _DEFAULT_LANGUAGE:
            return "system"
        if code not in self._available:
            _LOGGER.warning("Unsupported UI language %s; falling back to system", code)
            return "system"
        return code

    def _resolve_effective_language(self, requested: str) -> str:
        if requested != "system":
            return requested if requested in self._available else _DEFAULT_LANGUAGE

        locale_name = QLocale.system().name()
        if locale_name.startswith("de"):
            return "de"
        if locale_name in {"zh_CN", "zh_Hans_CN"} or locale_name.startswith("zh_Hans"):
            return "zh-CN"
        return _DEFAULT_LANGUAGE

    def _apply_formatter_locale(self, effective: str) -> None:
        info = self._available.get(effective)
        formatters.set_current_locale(info.qt_locale if info is not None else None)

    def _load_languages(self) -> dict[str, LanguageInfo]:
        try:
            with (
                resources.files(_RESOURCE_PACKAGE)
                .joinpath("languages.json")
                .open(
                    "r",
                    encoding="utf-8",
                ) as handle
            ):
                payload = json.load(handle)
        except Exception:  # noqa: BLE001 - resource loading must never block GUI startup.
            _LOGGER.warning("Unable to read bundled language metadata", exc_info=True)
            return {
                "system": LanguageInfo(
                    code="system",
                    native_name="English",
                    english_name="English",
                )
            }

        languages: dict[str, LanguageInfo] = {}
        for item in payload.get("languages", []):
            if not isinstance(item, dict):
                continue
            info = self._language_info_from_mapping(item)
            if info is not None:
                languages[info.code] = info
        languages.setdefault(
            "system",
            LanguageInfo(code="system", native_name="English", english_name="English"),
        )
        return languages

    def _language_info_from_mapping(self, item: dict[str, Any]) -> LanguageInfo | None:
        code = str(item.get("code") or "").strip()
        native_name = str(item.get("native_name") or "").strip()
        english_name = str(item.get("english_name") or "").strip()
        if not code or not native_name or not english_name:
            return None
        qt_locale = item.get("qt_locale")
        qm = item.get("qm")
        return LanguageInfo(
            code=code,
            native_name=native_name,
            english_name=english_name,
            qt_locale=str(qt_locale) if qt_locale else None,
            qm=str(qm) if qm else None,
        )

    def _load_translator(self, translator: QTranslator, filename: str) -> bool:
        try:
            resource = resources.files(_RESOURCE_PACKAGE).joinpath(filename)
            with resources.as_file(resource) as path:
                return bool(translator.load(str(Path(path))))
        except Exception:  # noqa: BLE001 - broken resource packages fall back to English.
            _LOGGER.warning("Unable to resolve translation resource %s", filename, exc_info=True)
            return False


__all__ = ["TranslationManager"]
