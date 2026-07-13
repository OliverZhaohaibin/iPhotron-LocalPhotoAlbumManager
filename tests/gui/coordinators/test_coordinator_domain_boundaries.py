from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

from iPhoto.gui.coordinators.recognition_coordinator import RecognitionCoordinator


ROOT = Path(__file__).resolve().parents[3]
COORDINATORS = ROOT / "src" / "iPhoto" / "gui" / "coordinators"
GUI_UI = ROOT / "src" / "iPhoto" / "gui" / "ui"


def test_navigation_depends_on_detail_navigation_port_only() -> None:
    source = (COORDINATORS / "navigation_coordinator.py").read_text(encoding="utf-8")

    assert "DetailNavigationPort" in source
    assert "PlaybackCoordinator" not in source
    assert "set_playback_coordinator" not in source


def test_gallery_has_no_optional_or_detail_domain_imports() -> None:
    source = (COORDINATORS / "gallery_coordinator.py").read_text(encoding="utf-8")

    for forbidden in (
        "detail_coordinator",
        "playback_coordinator",
        "recognition_coordinator",
        "location_info_coordinator",
        "osmand_search",
    ):
        assert forbidden not in source


def test_playback_does_not_import_optional_domain_implementations() -> None:
    source = (COORDINATORS / "playback_coordinator.py").read_text(encoding="utf-8")

    for forbidden in (
        "from maps.osmand_search import",
        "from iPhoto.gui.ui.tasks.info_panel_metadata_worker import",
        "from iPhoto.gui.ui.tasks.manual_face_add_worker import",
        "from iPhoto.people.pipeline import",
        "from iPhoto.pets.pipeline import",
    ):
        assert forbidden not in source


def test_desktop_runtime_import_keeps_optional_heavy_modules_unloaded() -> None:
    script = """
import sys
import iPhoto.gui.coordinators.desktop_coordinator_runtime
blocked = (
    'iPhoto.people.pipeline',
    'iPhoto.pets.pipeline',
    'iPhoto.gui.ui.tasks.manual_face_add_worker',
    'iPhoto.gui.ui.tasks.info_panel_metadata_worker',
    'iPhoto.gui.services.location_search_controller',
    'maps.osmand_search',
    'mapbox_vector_tile',
    'shapely',
)
raise SystemExit(1 if any(name in sys.modules for name in blocked) else 0)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")

    subprocess.run([sys.executable, "-c", script], env=env, check=True)


def test_recognition_first_creation_uses_latest_library_session() -> None:
    current_root = [Path("/library/new")]
    people_service = object()
    pet_service = object()
    detail = MagicMock()
    context = MagicMock()

    RecognitionCoordinator(
        context=context,
        detail=detail,
        pinned_items_service=MagicMock(),
        library_root_getter=lambda: current_root[0],
        people_service_getter=lambda *, library_root: (
            people_service if library_root == current_root[0] else None
        ),
        pet_service_getter=lambda *, library_root: (
            pet_service if library_root == current_root[0] else None
        ),
        cluster_callback=MagicMock(),
        group_callback=MagicMock(),
        pet_callback=MagicMock(),
    )

    detail.set_people_service.assert_called_once_with(people_service)
    detail.set_pet_service.assert_called_once_with(pet_service)
    detail.set_people_library_root.assert_called_once_with(Path("/library/new"))


def test_recognition_applies_hidden_people_preference_when_page_is_created() -> None:
    context = MagicMock()
    context.settings.get.return_value = "true"
    detail = MagicMock()
    people_page = MagicMock()

    coordinator = RecognitionCoordinator(
        context=context,
        detail=detail,
        pinned_items_service=MagicMock(),
        library_root_getter=lambda: Path("/library"),
        people_service_getter=lambda *, library_root: MagicMock(),
        pet_service_getter=lambda *, library_root: MagicMock(),
        cluster_callback=MagicMock(),
        group_callback=MagicMock(),
        pet_callback=MagicMock(),
    )

    coordinator.bind_people_page(people_page)

    context.settings.get.assert_called_once_with("ui.show_hidden_people", False)
    people_page.set_show_hidden_people.assert_called_once_with(True)


def test_main_coordinator_class_and_alias_are_removed() -> None:
    runtime_source = (COORDINATORS / "main_coordinator.py").read_text(encoding="utf-8")
    public_source = (COORDINATORS / "desktop_coordinator_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "class MainCoordinator" not in runtime_source
    assert "MainCoordinator =" not in runtime_source
    assert "MainCoordinator =" not in public_source


def test_main_window_and_window_manager_use_explicit_ports() -> None:
    main_window = (GUI_UI / "main_window.py").read_text(encoding="utf-8")
    window_manager = (GUI_UI / "window_manager.py").read_text(encoding="utf-8")

    assert "def bind_coordinators(self, lifecycle, gallery, detail)" in main_window
    assert "def set_coordinator(" not in main_window
    assert "self._coordinator_lifecycle.shutdown()" in main_window
    assert "self._gallery_coordinator.open_album_from_path(path)" in main_window
    assert "def set_detail_coordinator(" in window_manager
    assert "def set_controller(" not in window_manager
