from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for i18n tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)
pytest.importorskip("PySide6.QtTest", reason="Qt test helpers not available", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QFont
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QWidget

from iPhoto.gui.i18n import TranslationManager, ui_font
from iPhoto.gui.i18n.language import LanguageInfo
from iPhoto.gui.ui.main_window import MainWindow
from iPhoto.gui.ui.widgets.info_panel import InfoPanel
from iPhoto.gui.ui.widgets.main_header import MainHeaderWidget
from iPhoto.gui.ui.widgets.player_bar import PlayerBar
from iPhoto.settings.manager import SettingsManager
from iPhoto.settings.schema import merge_with_defaults, validate_settings


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _settings(tmp_path: Path) -> SettingsManager:
    manager = SettingsManager(path=tmp_path / "settings.json")
    manager.load()
    return manager


@pytest.fixture()
def preserved_app_font(qapp: QApplication):
    original = QFont(qapp.font())
    ui_font._ORIGINAL_APP_FONT = None
    ui_font._FALLBACKS_CONFIGURED = False
    yield
    qapp.setFont(original)
    ui_font._ORIGINAL_APP_FONT = None
    ui_font._FALLBACKS_CONFIGURED = False


class _RetranslateControl:
    def setToolTip(self, _text: str) -> None:
        pass

    def setText(self, _text: str) -> None:
        pass


def _detail_page_retranslate_harness(placeholder_text: str, standard_text: str):
    from iPhoto.gui.ui.widgets.detail_page import DetailPageWidget

    widget = DetailPageWidget.__new__(DetailPageWidget)
    control = _RetranslateControl()
    widget.back_button = control
    widget.zoom_out_button = control
    widget.zoom_slider = control
    widget.zoom_in_button = control
    widget.info_button = control
    widget.share_button = control
    widget.favorite_button = control
    widget.rotate_left_button = control
    widget.edit_button = control
    widget.edit_rotate_left_button = control
    widget.player_bar = SimpleNamespace(retranslate_ui=lambda: None)
    widget.player_stack = QStackedWidget()
    widget.player_placeholder = QLabel(placeholder_text)
    widget.player_stack.addWidget(widget.player_placeholder)
    widget.player_stack.setCurrentWidget(widget.player_placeholder)
    widget._placeholder_default_text = standard_text
    return widget, DetailPageWidget


def test_settings_language_default_and_schema() -> None:
    merged = merge_with_defaults({"ui": {"theme": "dark"}})

    assert merged["ui"]["language"] == "system"
    validate_settings({**merged, "ui": {**merged["ui"], "language": "de"}})
    validate_settings({**merged, "ui": {**merged["ui"], "language": "zh-CN"}})

    invalid = {**merged, "ui": {**merged["ui"], "language": "fr"}}
    with pytest.raises(Exception):
        validate_settings(invalid)


def test_translation_manager_reads_languages_and_switches_to_chinese(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    manager = _settings(tmp_path)
    translations = TranslationManager(manager)
    spy = QSignalSpy(translations.languageChanged)

    translations.apply_language()
    translations.set_language("zh-CN")
    qapp.processEvents()

    codes = {item.code for item in translations.available_languages()}
    assert {"system", "de", "zh-CN"}.issubset(codes)
    assert manager.get("ui.language") == "zh-CN"
    assert translations.current_language() == "zh-CN"
    assert translations.effective_language() == "zh-CN"
    assert spy.count() >= 1
    assert QCoreApplication.translate("MainHeader", "Language", None) == "语言"
    assert QCoreApplication.translate("InfoPanel", "Download Map Extension", None) == "下载地图扩展"
    assert QCoreApplication.translate("AlbumSidebar", "Pin Album", None) == "固定相册"
    assert QCoreApplication.translate("GalleryMenu", "Export", None) == "导出"
    assert QCoreApplication.translate("GalleryContextMenu", "Deleted", None) == "已删除"
    assert QCoreApplication.translate("DetailPage", "Rotate Left", None) == "向左旋转"
    assert (
        QCoreApplication.translate("PlaybackCoordinator", "Writing data, please wait...", None)
        == "正在写入数据，请稍候…"
    )
    assert QCoreApplication.translate("PlayerBar", "Volume", None) == "音量"
    assert QCoreApplication.translate("EditSidebar", "Light", None) == "光效"
    assert QCoreApplication.translate("EditLight", "Brilliance", None) == "鲜明度"
    assert QCoreApplication.translate("EditBW", "Neutrals", None) == "中性"
    assert QCoreApplication.translate("EditPerspective", "Aspect", None) == "宽高比"
    assert (
        QCoreApplication.translate("ShareController", "Copied to Clipboard", None)
        == "已复制到剪贴板"
    )
    assert QCoreApplication.translate("MainCoordinator", "Moved", None) == "已移动"
    assert QCoreApplication.translate("InformationPopup", "Information", None) == "信息"
    assert QCoreApplication.translate("GalleryPage", "Return to Map", None) == "返回地图"

    panel = InfoPanel()
    try:
        panel.set_location_capability(enabled=False)
        panel.set_asset_metadata({"rel": "photo.jpg", "name": "photo.jpg"})
        panel.retranslate_ui()

        assert panel._title_label.text() == "信息"
        assert panel._location_download_button.text() == "下载地图扩展"
        assert panel._location_editor.placeholderText() == "分配位置"
    finally:
        panel.close()

    player_bar = PlayerBar()
    try:
        player_bar.retranslate_ui()

        assert player_bar._volume_slider.toolTip() == "音量"
        assert player_bar._mute_button.toolTip() == "静音"
    finally:
        player_bar.close()


def test_windows_chinese_font_prefers_english_yahei_name(
    monkeypatch,
    qapp: QApplication,
    preserved_app_font,
) -> None:
    qapp.setFont(QFont("Arial"))
    monkeypatch.setattr(ui_font.sys, "platform", "win32")
    monkeypatch.setattr(
        ui_font.QFontDatabase,
        "families",
        staticmethod(lambda: ["微软雅黑", "Microsoft Yahei"]),
    )

    ui_font.apply_language_font("zh-CN")

    assert qapp.font().family() == "Microsoft Yahei"


def test_windows_chinese_font_uses_chinese_yahei_name(
    monkeypatch,
    qapp: QApplication,
    preserved_app_font,
) -> None:
    qapp.setFont(QFont("Arial"))
    monkeypatch.setattr(ui_font.sys, "platform", "win32")
    monkeypatch.setattr(ui_font.QFontDatabase, "families", staticmethod(lambda: ["微软雅黑"]))

    ui_font.apply_language_font("zh-CN")

    assert qapp.font().family() == "微软雅黑"


def test_non_windows_chinese_font_does_not_change_app_font(
    monkeypatch,
    qapp: QApplication,
    preserved_app_font,
) -> None:
    qapp.setFont(QFont("Arial"))
    monkeypatch.setattr(ui_font.sys, "platform", "darwin")
    monkeypatch.setattr(
        ui_font.QFontDatabase,
        "families",
        staticmethod(lambda: ["Microsoft Yahei"]),
    )

    ui_font.apply_language_font("zh-CN")

    assert qapp.font().family() == "Arial"


def test_linux_font_fallback_uses_noto_sans_cjk_sc_when_available(
    monkeypatch,
    qapp: QApplication,
    preserved_app_font,
) -> None:
    qapp.setFont(QFont("Arial"))
    monkeypatch.setattr(ui_font.sys, "platform", "linux")

    def fake_families(_writing_system=None):
        return ["Noto Sans", "Noto Sans CJK SC"]

    monkeypatch.setattr(ui_font.QFontDatabase, "families", staticmethod(fake_families))

    ui_font.configure_application_font_fallbacks()

    assert qapp.font().family() == "Arial"
    assert qapp.font().families() == ["Arial", "Noto Sans CJK SC"]


def test_linux_font_fallback_keeps_current_font_when_noto_cjk_is_missing(
    monkeypatch,
    qapp: QApplication,
    preserved_app_font,
) -> None:
    qapp.setFont(QFont("Arial"))
    monkeypatch.setattr(ui_font.sys, "platform", "linux")

    def fake_families(_writing_system=None):
        return ["Noto Sans", "DejaVu Sans"]

    monkeypatch.setattr(ui_font.QFontDatabase, "families", staticmethod(fake_families))

    ui_font.configure_application_font_fallbacks()

    assert qapp.font().family() == "Arial"
    assert qapp.font().families() == ["Arial"]


def test_linux_font_fallback_is_idempotent(
    monkeypatch,
    qapp: QApplication,
    preserved_app_font,
) -> None:
    qapp.setFont(QFont("Arial"))
    monkeypatch.setattr(ui_font.sys, "platform", "linux")

    def fake_families(_writing_system=None):
        return ["Noto Sans CJK SC"]

    monkeypatch.setattr(ui_font.QFontDatabase, "families", staticmethod(fake_families))

    ui_font.configure_application_font_fallbacks()
    ui_font._FALLBACKS_CONFIGURED = False
    ui_font.configure_application_font_fallbacks()

    assert qapp.font().family() == "Arial"
    assert qapp.font().families() == ["Arial", "Noto Sans CJK SC"]


def test_linux_language_switch_does_not_replace_app_font(
    monkeypatch,
    tmp_path: Path,
    qapp: QApplication,
    preserved_app_font,
) -> None:
    qapp.setFont(QFont("Arial"))
    monkeypatch.setattr(ui_font.sys, "platform", "linux")
    monkeypatch.setattr(
        ui_font.QFontDatabase,
        "families",
        staticmethod(lambda: ["Noto Sans CJK SC"]),
    )
    translations = TranslationManager(_settings(tmp_path))

    translations.apply_language("zh-CN")
    qapp.processEvents()

    assert qapp.font().family() == "Arial"
    assert qapp.font().families() == ["Arial"]


def test_windows_chinese_font_restores_original_when_language_changes(
    monkeypatch,
    qapp: QApplication,
    preserved_app_font,
) -> None:
    qapp.setFont(QFont("Arial"))
    monkeypatch.setattr(ui_font.sys, "platform", "win32")
    monkeypatch.setattr(
        ui_font.QFontDatabase,
        "families",
        staticmethod(lambda: ["Microsoft Yahei"]),
    )

    ui_font.apply_language_font("zh-CN")
    ui_font.apply_language_font("de")

    assert qapp.font().family() == "Arial"


def test_detail_page_retranslate_updates_standard_placeholder_only(
    monkeypatch,
    qapp: QApplication,
) -> None:
    widget, detail_page_cls = _detail_page_retranslate_harness(
        "Select a photo or video to preview.",
        "Select a photo or video to preview.",
    )
    monkeypatch.setattr(
        detail_page_cls,
        "default_placeholder_text",
        classmethod(lambda _cls: "Wählen Sie ein Foto oder Video aus."),
    )

    widget.retranslate_ui()

    assert widget.player_placeholder.text() == "Wählen Sie ein Foto oder Video aus."
    assert widget._placeholder_default_text == "Wählen Sie ein Foto oder Video aus."


def test_detail_page_retranslate_preserves_custom_placeholder_message(
    monkeypatch,
    qapp: QApplication,
) -> None:
    widget, detail_page_cls = _detail_page_retranslate_harness(
        "Writing data, please wait...",
        "Select a photo or video to preview.",
    )
    monkeypatch.setattr(
        detail_page_cls,
        "default_placeholder_text",
        classmethod(lambda _cls: "Wählen Sie ein Foto oder Video aus."),
    )

    widget.retranslate_ui()

    assert widget.player_placeholder.text() == "Writing data, please wait..."
    assert widget._placeholder_default_text == "Wählen Sie ein Foto oder Video aus."


def test_translation_manager_falls_back_when_qm_is_missing(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    manager = _settings(tmp_path)
    translations = TranslationManager(manager)
    translations._available["de"] = LanguageInfo(
        code="de",
        native_name="Deutsch",
        english_name="German",
        qt_locale="de_DE",
        qm="missing.qm",
    )

    translations.apply_language("de")
    qapp.processEvents()

    assert translations.current_language() == "de"
    assert translations.effective_language() == "en"


def test_main_header_language_menu_actions_are_exclusive(qapp: QApplication) -> None:
    parent = QWidget()
    try:
        header = MainHeaderWidget(None, parent)
        language_actions = header.language_group.actions()

        assert header.language_menu.title() == "Language"
        assert [action.data() for action in language_actions] == ["system", "de", "zh-CN"]
        assert [action.text() for action in language_actions] == [
            "English",
            "Deutsch",
            "简体中文",
        ]
        header.language_de.setChecked(True)
        assert header.language_de.isChecked()
        assert not header.language_system.isChecked()
    finally:
        parent.deleteLater()


def test_main_window_language_change_queues_retranslate(monkeypatch, qapp: QApplication) -> None:
    calls: list[str] = []
    window = MainWindow.__new__(MainWindow)
    window.ui = type(
        "FakeUi",
        (),
        {
            "main_header": object(),
            "retranslateUi": lambda _ui, _window: calls.append("ui"),
        },
    )()
    window.window_manager = None
    monkeypatch.setattr(window, "findChildren", lambda _type: [])

    window._schedule_retranslate_ui_tree()
    qapp.processEvents()

    assert calls == ["ui"]
