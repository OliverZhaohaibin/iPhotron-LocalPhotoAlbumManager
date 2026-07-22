from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from iPhoto.gui.coordinators.recognition_coordinator import (
    RecognitionCoordinator,
    _DashboardSnapshot,
)

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


def test_people_warmup_does_not_materialize_hidden_qwidgets() -> None:
    source = (COORDINATORS / "desktop_coordinator_runtime.py").read_text(encoding="utf-8")

    assert "threading.Thread" not in source
    assert "PeopleDashboardModuleWarmup" not in source
    assert "_materialize_people_dashboard" not in source
    assert 'ui.ensure_feature("people")' not in source


def test_recognition_read_surface_does_not_import_ai_pipelines() -> None:
    script = """
import sys
import tempfile
from pathlib import Path
from iPhoto.bootstrap.library_session import LibrarySession
with tempfile.TemporaryDirectory() as directory:
    session = LibrarySession(Path(directory))
    session.recognition_queries
    blocked = ('iPhoto.people.pipeline', 'iPhoto.pets.pipeline')
    if any(name in sys.modules for name in blocked):
        raise SystemExit(1)
    session.shutdown()
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


def test_recognition_rebinds_runtime_services_after_library_change() -> None:
    roots = [Path("/library/first")]
    people_services = {root: MagicMock() for root in roots}
    pet_services = {root: MagicMock() for root in roots}
    context = MagicMock()
    coordinator = RecognitionCoordinator(
        context=context,
        detail=MagicMock(),
        pinned_items_service=MagicMock(),
        library_root_getter=lambda: roots[0],
        people_service_getter=lambda *, library_root: people_services[library_root],
        pet_service_getter=lambda *, library_root: pet_services[library_root],
        cluster_callback=MagicMock(),
        group_callback=MagicMock(),
        pet_callback=MagicMock(),
    )
    context.library.bind_recognition_services.reset_mock()

    next_root = Path("/library/second")
    roots[0] = next_root
    people_services[next_root] = MagicMock()
    pet_services[next_root] = MagicMock()
    coordinator.rebind_library()

    context.library.bind_recognition_services.assert_called_once_with(
        people_services[next_root],
        pet_services[next_root],
    )


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


def test_recognition_binds_once_and_defers_ai_until_visible_first_viewport() -> None:
    context = MagicMock()
    detail = MagicMock()
    people_service = MagicMock()
    pet_service = MagicMock()
    people_page = MagicMock()
    coordinator = RecognitionCoordinator(
        context=context,
        detail=detail,
        pinned_items_service=MagicMock(),
        library_root_getter=lambda: Path("/library"),
        people_service_getter=lambda *, library_root: people_service,
        pet_service_getter=lambda *, library_root: pet_service,
        cluster_callback=MagicMock(),
        group_callback=MagicMock(),
        pet_callback=MagicMock(),
    )

    coordinator.bind_people_page(people_page)

    context.library.bind_recognition_services.assert_called_once_with(
        people_service,
        pet_service,
    )
    people_page.set_services.assert_called_once()
    people_page.set_people_service.assert_not_called()
    people_page.set_pet_service.assert_not_called()
    context.library.activate_recognition_scans.assert_not_called()
    ready_callback = people_page.firstViewportReady.connect.call_args.args[0]
    with patch(
        "iPhoto.gui.coordinators.recognition_coordinator.QTimer.singleShot",
        side_effect=lambda _delay, callback: callback(),
    ):
        ready_callback(1)
        context.library.activate_recognition_scans.assert_not_called()
        coordinator.people_view_shown()

    context.library.activate_recognition_scans.assert_called_once_with()


def test_recognition_page_reuses_inflight_warmup_query() -> None:
    context = MagicMock()
    detail = MagicMock()
    people_page = MagicMock()
    root = Path("/library")
    coordinator = RecognitionCoordinator(
        context=context,
        detail=detail,
        pinned_items_service=MagicMock(),
        library_root_getter=lambda: root,
        people_service_getter=lambda *, library_root: MagicMock(),
        pet_service_getter=lambda *, library_root: MagicMock(),
        cluster_callback=MagicMock(),
        group_callback=MagicMock(),
        pet_callback=MagicMock(),
    )
    coordinator._warmup_root = root

    coordinator.bind_people_page(people_page)

    assert people_page.set_services.call_args.kwargs["reload"] is False
    people_page.reload.assert_not_called()


def test_people_dashboard_warmup_starts_only_when_feature_is_bound() -> None:
    from iPhoto.gui.coordinators.desktop_coordinator_runtime import (
        DesktopCoordinatorRuntime,
    )

    runtime = DesktopCoordinatorRuntime.__new__(DesktopCoordinatorRuntime)
    runtime._people_view_activation_bound = True
    recognition = MagicMock()
    runtime._ensure_recognition_coordinator = MagicMock(return_value=recognition)
    people_page = MagicMock()

    DesktopCoordinatorRuntime._bind_people_feature(runtime, people_page)

    runtime._ensure_recognition_coordinator.assert_called_once_with()
    recognition.assert_has_calls(
        [call.warm_dashboard_snapshot(), call.bind_people_page(people_page)]
    )


def test_recognition_shutdown_rejects_queued_warmup_result() -> None:
    root = Path("/library")
    context = MagicMock()
    coordinator = RecognitionCoordinator(
        context=context,
        detail=MagicMock(),
        pinned_items_service=MagicMock(),
        library_root_getter=lambda: root,
        people_service_getter=lambda *, library_root: MagicMock(),
        pet_service_getter=lambda *, library_root: MagicMock(),
        cluster_callback=MagicMock(),
        group_callback=MagicMock(),
        pet_callback=MagicMock(),
    )
    people_page = MagicMock()
    coordinator._people_page = people_page
    snapshot = _DashboardSnapshot(
        root=root,
        generation=coordinator._warmup_generation,
        index_version=0,
        include_hidden=False,
        summaries=(),
        groups=(),
        pet_summaries=(),
        pending=0,
        pet_pending=0,
        status_message=None,
        pet_status_message=None,
    )

    coordinator.shutdown()
    coordinator._on_dashboard_warmup_ready(snapshot)

    people_page.apply_snapshot.assert_not_called()


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
