from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import extract_i18n_strings  # noqa: E402


def _write_source(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _message_sources(ts_file: Path) -> set[str]:
    root = ET.parse(ts_file).getroot()
    return {source.text or "" for source in root.findall(".//message/source")}


def _translations_for_context(ts_file: Path, context_name: str) -> dict[str, str]:
    root = ET.parse(ts_file).getroot()
    for context in root.findall(".//context"):
        if context.findtext("name") != context_name:
            continue
        return {
            message.findtext("source") or "": message.findtext("translation") or ""
            for message in context.findall("message")
        }
    return {}


def test_extracts_qcore_translate_and_project_tr_calls(tmp_path: Path) -> None:
    source = tmp_path / "src" / "widget.py"
    _write_source(
        source,
        """
from PySide6.QtCore import QCoreApplication
from iPhoto.gui.i18n import tr

title = QCoreApplication.translate("MainWindow", "Open Album Folder…", None)
label = tr("PeopleDashboard", "People")
""",
    )

    messages = extract_i18n_strings.extract_messages([tmp_path / "src"])

    assert extract_i18n_strings.MessageKey("MainWindow", "Open Album Folder…") in messages
    assert extract_i18n_strings.MessageKey("PeopleDashboard", "People") in messages


def test_extracts_local_qcore_translate_alias(tmp_path: Path) -> None:
    source = tmp_path / "src" / "widget.py"
    _write_source(
        source,
        """
from PySide6.QtCore import QCoreApplication

tr = QCoreApplication.translate
text = tr("InfoPanel", "No photo selected", None)
""",
    )

    messages = extract_i18n_strings.extract_messages([tmp_path / "src"])

    assert messages == [extract_i18n_strings.MessageKey("InfoPanel", "No photo selected")]


def test_extracts_fixed_context_instance_helper_calls(tmp_path: Path) -> None:
    source = tmp_path / "src" / "status_bar_controller.py"
    _write_source(
        source,
        """
from PySide6.QtCore import QCoreApplication

class StatusBarController:
    def _tr(self, source_text: str) -> str:
        return QCoreApplication.translate("StatusBar", source_text, None)

    def begin_scan(self) -> None:
        self.show_message(self._tr("Starting scan…"))
""",
    )

    messages = extract_i18n_strings.extract_messages([tmp_path / "src"])

    assert messages == [extract_i18n_strings.MessageKey("StatusBar", "Starting scan…")]


def test_extracts_fixed_context_helpers_per_class_scope(tmp_path: Path) -> None:
    source = tmp_path / "src" / "map_extension_download_controller.py"
    _write_source(
        source,
        """
from PySide6.QtCore import QCoreApplication

class ProgressDialog:
    def _tr(self, source_text: str) -> str:
        return QCoreApplication.translate("MapExtensionProgress", source_text, None)

    def retranslate_ui(self) -> None:
        self.setWindowTitle(self._tr("Downloading"))

class MapExtensionDownloadController:
    def _tr(self, source_text: str) -> str:
        return QCoreApplication.translate("MapExtension", source_text, None)

    def maybe_prompt_on_startup(self) -> None:
        self.show_message(self._tr("Map Extension"))
""",
    )

    messages = extract_i18n_strings.extract_messages([tmp_path / "src"])

    assert messages == [
        extract_i18n_strings.MessageKey("MapExtensionProgress", "Downloading"),
        extract_i18n_strings.MessageKey("MapExtension", "Map Extension"),
    ]


def test_marks_plural_messages_when_n_is_variable(tmp_path: Path) -> None:
    source = tmp_path / "src" / "gallery.py"
    _write_source(
        source,
        """
from PySide6.QtCore import QCoreApplication

count = selected_count()
text = QCoreApplication.translate("Gallery", "%n item(s) selected", None, count)
""",
    )

    messages = extract_i18n_strings.extract_messages([tmp_path / "src"])

    assert messages == [
        extract_i18n_strings.MessageKey(
            "Gallery",
            "%n item(s) selected",
            numerus=True,
        )
    ]


def test_skips_dynamic_context_and_text(tmp_path: Path) -> None:
    source = tmp_path / "src" / "widget.py"
    _write_source(
        source,
        """
from PySide6.QtCore import QCoreApplication

context = "InfoPanel"
text = "No photo selected"
QCoreApplication.translate(context, "Static source", None)
QCoreApplication.translate("InfoPanel", text, None)
QCoreApplication.translate("InfoPanel", f"Dynamic {text}", None)
""",
    )

    messages = extract_i18n_strings.extract_messages([tmp_path / "src"])

    assert messages == []


def test_update_ts_preserves_existing_translation_and_marks_new_unfinished(
    tmp_path: Path,
) -> None:
    ts_file = tmp_path / "iPhoto_de.ts"
    ts_file.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<TS version="2.1" language="de_DE">
    <context>
        <name>MainHeader</name>
        <message>
            <source>Language</source>
            <translation>Sprache</translation>
        </message>
    </context>
</TS>
""",
        encoding="utf-8",
    )

    extract_i18n_strings.update_ts(
        ts_file,
        [
            extract_i18n_strings.MessageKey("MainHeader", "Language"),
            extract_i18n_strings.MessageKey("MainHeader", "System"),
        ],
    )

    root = ET.parse(ts_file).getroot()
    messages = {
        element.findtext("source"): element.find("translation")
        for element in root.findall(".//message")
    }

    assert messages["Language"] is not None
    assert messages["Language"].text == "Sprache"
    assert messages["Language"].get("type") is None
    assert messages["System"] is not None
    assert messages["System"].get("type") == "unfinished"


def test_update_ts_adds_comment_and_numerus_metadata(tmp_path: Path) -> None:
    ts_file = tmp_path / "iPhoto_de.ts"

    extract_i18n_strings.update_ts(
        ts_file,
        [
            extract_i18n_strings.MessageKey(
                "Gallery",
                "%n item(s) selected",
                comment="Selection count",
                numerus=True,
            )
        ],
        language="de_DE",
    )

    root = ET.parse(ts_file).getroot()
    message = root.find(".//message")

    assert root.get("language") == "de_DE"
    assert message is not None
    assert message.get("numerus") == "yes"
    assert message.findtext("comment") == "Selection count"
    assert _message_sources(ts_file) == {"%n item(s) selected"}


def test_apple_photos_edit_terms_are_translated() -> None:
    """Edit translations should follow the Apple Photos support terminology."""

    i18n_dir = Path(__file__).parent.parent / "src" / "iPhoto" / "resources" / "i18n"
    zh_terms = _translations_for_context(i18n_dir / "iPhoto_zh_CN.ts", "EditSelectiveColor")
    de_terms = _translations_for_context(i18n_dir / "iPhoto_de.ts", "EditSelectiveColor")
    zh_wb_terms = _translations_for_context(i18n_dir / "iPhoto_zh_CN.ts", "EditWB")
    de_wb_terms = _translations_for_context(i18n_dir / "iPhoto_de.ts", "EditWB")

    assert zh_terms["Hue"] == "色调"
    assert zh_terms["Luminance"] == "亮度"
    assert de_terms["Hue"] == "Farbton"
    assert de_terms["Luminance"] == "Leuchtkraft"
    assert zh_wb_terms["Neutral Gray"] == "中性灰色"
    assert zh_wb_terms["Temperature/Tint"] == "色温/色调"
    assert de_wb_terms["Neutral Gray"] == "Neutrales Grau"
    assert de_wb_terms["Temperature/Tint"] == "Temperatur/Farbton"


def test_maps_preview_terms_are_translated() -> None:
    """Standalone map preview translations should stay complete."""

    i18n_dir = Path(__file__).parent.parent / "src" / "iPhoto" / "resources" / "i18n"
    zh_terms = _translations_for_context(i18n_dir / "iPhoto_zh_CN.ts", "MapsPreview")
    de_terms = _translations_for_context(i18n_dir / "iPhoto_de.ts", "MapsPreview")
    zh_cli_terms = _translations_for_context(i18n_dir / "iPhoto_zh_CN.ts", "MapsPreviewCLI")
    de_cli_terms = _translations_for_context(i18n_dir / "iPhoto_de.ts", "MapsPreviewCLI")

    assert zh_terms["Map Preview - {backend} - Zoom {zoom}"] == "地图预览 - {backend} - 缩放 {zoom}"
    assert zh_terms["OBF Raster"] == "OBF 栅格"
    assert de_terms["Map Preview - {backend} - Zoom {zoom}"] == "Kartenvorschau - {backend} - Zoom {zoom}"
    assert de_terms["OBF Raster"] == "OBF-Raster"
    assert zh_cli_terms["Preview OsmAnd or legacy map backends"] == "预览 OsmAnd 或旧版地图后端"
    assert de_cli_terms["Preview OsmAnd or legacy map backends"] == (
        "OsmAnd- oder alte Karten-Backends in der Vorschau anzeigen"
    )
