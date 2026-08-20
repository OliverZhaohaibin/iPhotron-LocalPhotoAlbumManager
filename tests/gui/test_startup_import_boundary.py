from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_gui_entry_import_keeps_heavy_features_unloaded() -> None:
    project_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(project_root / "src")
    script = """
import sys
import iPhoto.gui.main
blocked = (
    'iPhoto.gui.facade',
    'numpy',
    'PySide6.QtMultimedia',
    'iPhoto.people.pipeline',
    'maps.map_widget.map_renderer',
    'iPhoto.gui.coordinators.main_coordinator',
)
loaded = [name for name in blocked if name in sys.modules]
if loaded:
    raise SystemExit(','.join(loaded))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_main_window_import_keeps_optional_features_unloaded() -> None:
    project_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(project_root / "src")
    script = """
import sys
from iPhoto.gui.ui.main_window import MainWindow
blocked = (
    'numpy',
    'PySide6.QtMultimedia',
    'iPhoto.people.pipeline',
    'iPhoto.gui.ui.models.edit_session',
    'iPhoto.gui.services.asset_import_service',
    'maps.map_widget.map_renderer',
)
loaded = [name for name in blocked if name in sys.modules]
if loaded:
    raise SystemExit(','.join(loaded))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_detail_surface_modules_do_not_start_qt_multimedia() -> None:
    project_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(project_root / "src")
    script = """
import sys
import iPhoto.gui.ui.widgets.detail_page
blocked = ('PySide6.QtMultimedia',)
loaded = [name for name in blocked if name in sys.modules]
if loaded:
    raise SystemExit(','.join(loaded))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_detail_hierarchy_precedes_native_menu_bar_creation() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src/iPhoto/gui/ui/ui_main_window.py"
    ).read_text(encoding="utf-8")

    prepare_call = source.index("self.prepare_detail_native_hierarchy()")
    menu_bar_creation = source.index(
        "self.main_header = MainHeaderWidget(self.window_shell, MainWindow)"
    )
    assert prepare_call < menu_bar_creation
