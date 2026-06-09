from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"
SRC_ROOT = Path(__file__).parent.parent.parent / "src"

sys.path.insert(0, str(TOOLS_DIR))

import check_i18n_strings  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_i18n_string_gate_blocks_high_risk_direct_literals(tmp_path: Path) -> None:
    source = tmp_path / "src" / "widget.py"
    _write(
        source,
        """
class Widget:
    def build(self):
        self.button.setToolTip("Return to Map")
        self.statusBar().showMessage("Moved")
""",
    )

    violations = check_i18n_strings.check([tmp_path / "src"])

    assert [violation.text for violation in violations] == ["Return to Map", "Moved"]


def test_i18n_string_gate_allows_translated_calls_and_symbols(tmp_path: Path) -> None:
    source = tmp_path / "src" / "widget.py"
    _write(
        source,
        """
from iPhoto.gui.i18n import tr

class Widget:
    def build(self):
        self.button.setToolTip(tr("GalleryPage", "Return to Map"))
        self.play_button.setText("▶")
        self.language_de.setText("Deutsch")
""",
    )

    assert check_i18n_strings.check([tmp_path / "src"]) == []


def test_i18n_string_gate_current_gui_sources_are_clean() -> None:
    violations = check_i18n_strings.check(
        [
            SRC_ROOT / "iPhoto" / "gui",
            SRC_ROOT / "maps",
        ],
    )

    assert not violations, (
        "Untranslated GUI strings found:\n"
        + "\n".join(f"  {violation}" for violation in violations)
    )
