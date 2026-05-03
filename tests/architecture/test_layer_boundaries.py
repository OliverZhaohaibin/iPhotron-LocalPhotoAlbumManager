"""Architecture regression: vNext layer boundaries stay enforced."""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"
SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "iPhoto"

sys.path.insert(0, str(TOOLS_DIR))

import check_layer_boundaries  # noqa: E402


def test_vnext_layer_boundaries() -> None:
    violations = check_layer_boundaries.check(SRC_ROOT)

    assert not violations, (
        "vNext layer boundary violations:\n"
        + "\n".join(f"  {item}" for item in violations)
    )


def test_legacy_use_case_package_import_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "iPhoto"
    module = source / "gui" / "example.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from iPhoto.application.use_cases import ScanAlbumUseCase\n",
        encoding="utf-8",
    )

    violations = check_layer_boundaries.check(source)

    assert any(
        "runtime imports legacy domain-repository use case "
        "iPhoto.application.use_cases" in violation
        for violation in violations
    )


def test_gui_viewmodel_domain_repository_import_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "iPhoto"
    module = source / "gui" / "viewmodels" / "example.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from iPhoto.domain.repositories import IAssetRepository\n",
        encoding="utf-8",
    )

    violations = check_layer_boundaries.check(source)

    assert any(
        "GUI collection/viewmodel imports legacy domain repository "
        "iPhoto.domain.repositories" in violation
        for violation in violations
    )


def test_gui_file_operation_service_planning_import_is_blocked(
    tmp_path: Path,
) -> None:
    source = tmp_path / "iPhoto"
    module = source / "gui" / "services" / "restoration_service.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from iPhoto.bootstrap.library_asset_lifecycle_service import LibraryAssetLifecycleService\n"
        "from iPhoto.media_classifier import IMAGE_EXTENSIONS\n",
        encoding="utf-8",
    )

    violations = check_layer_boundaries.check(source)

    assert any(
        "GUI file-operation service imports session planning dependency "
        "iPhoto.bootstrap.library_asset_lifecycle_service" in violation
        for violation in violations
    )
    assert any(
        "GUI file-operation service imports session planning dependency "
        "iPhoto.media_classifier" in violation
        for violation in violations
    )


def test_gui_runtime_backend_import_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "iPhoto"
    module = source / "gui" / "coordinators" / "example.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from iPhoto import app as backend\n",
        encoding="utf-8",
    )

    violations = check_layer_boundaries.check(source)

    assert any(
        "GUI runtime imports compatibility backend iPhoto.app" in violation
        for violation in violations
    )


def test_gui_people_bootstrap_factory_import_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "iPhoto"
    module = source / "gui" / "coordinators" / "example.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from iPhoto.bootstrap.library_people_service import create_people_service\n",
        encoding="utf-8",
    )

    violations = check_layer_boundaries.check(source)

    assert any(
        "GUI runtime imports People bootstrap factory "
        "iPhoto.bootstrap.library_people_service" in violation
        for violation in violations
    )


def test_gui_sidecar_import_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "iPhoto"
    module = source / "gui" / "viewmodels" / "example.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from iPhoto.io import sidecar\n",
        encoding="utf-8",
    )

    violations = check_layer_boundaries.check(source)

    assert any(
        "GUI runtime imports edit sidecar implementation iPhoto.io.sidecar"
        in violation
        for violation in violations
    )


def test_gui_location_helper_import_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "iPhoto"
    module = source / "gui" / "viewmodels" / "example.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from iPhoto.library.geo_aggregator import geotagged_asset_from_row\n",
        encoding="utf-8",
    )

    violations = check_layer_boundaries.check(source)

    assert any(
        "GUI runtime imports legacy location helper iPhoto.library.geo_aggregator"
        in violation
        for violation in violations
    )


def test_map_widgets_must_use_factory_for_concrete_widget_imports(tmp_path: Path) -> None:
    source = tmp_path / "iPhoto"
    module = source / "gui" / "ui" / "widgets" / "photo_map_view.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from maps.map_widget.map_gl_widget import MapGLWidget\n",
        encoding="utf-8",
    )

    violations = check_layer_boundaries.check(source)

    assert any(
        "map widget construction must go through map_widget_factory"
        in violation
        for violation in violations
    )


def test_gui_library_update_service_worker_import_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "iPhoto"
    module = source / "gui" / "services" / "library_update_service.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from iPhoto.library.workers.scanner_worker import ScannerWorker\n",
        encoding="utf-8",
    )

    violations = check_layer_boundaries.check(source)

    assert any(
        "GUI library update service imports worker implementation detail "
        "iPhoto.library.workers.scanner_worker" in violation
        for violation in violations
    )


def test_gui_library_session_service_fallback_construction_is_blocked(
    tmp_path: Path,
) -> None:
    source = tmp_path / "iPhoto"
    module = source / "gui" / "services" / "example.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from iPhoto.bootstrap.library_scan_service import LibraryScanService\n"
        "service = LibraryScanService('/tmp/library')\n",
        encoding="utf-8",
    )

    violations = check_layer_boundaries.check(source)

    assert any(
        "GUI/library runtime constructs session service fallback directly via "
        "LibraryScanService" in violation
        for violation in violations
    )
