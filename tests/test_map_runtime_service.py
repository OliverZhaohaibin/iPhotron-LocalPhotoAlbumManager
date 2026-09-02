from __future__ import annotations

from pathlib import Path

from maps.map_sources import MapSourceSpec

from iPhoto.infrastructure.services import map_runtime_service as map_runtime_service_module
from iPhoto.infrastructure.services.map_runtime_service import SessionMapRuntimeService


def test_map_runtime_service_keeps_native_widget_available_when_macos_python_gl_probe_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(map_runtime_service_module, "_has_qt_application", lambda: True)
    monkeypatch.setattr(map_runtime_service_module, "check_opengl_support", lambda: False)
    monkeypatch.setattr(map_runtime_service_module, "prefer_osmand_native_widget", lambda: True)
    monkeypatch.setattr(map_runtime_service_module, "has_usable_osmand_native_widget", lambda root: root == tmp_path)
    monkeypatch.setattr(map_runtime_service_module, "probe_native_widget_runtime", lambda root: (root == tmp_path, None))
    monkeypatch.setattr(
        map_runtime_service_module,
        "choose_default_map_source",
        lambda root, **_kwargs: MapSourceSpec.osmand_default(root),
    )
    monkeypatch.setattr(
        map_runtime_service_module,
        "has_usable_osmand_search_extension",
        lambda package_root=None: False,
    )

    capabilities = SessionMapRuntimeService(tmp_path).capabilities()

    assert capabilities.python_gl_available is False
    assert capabilities.native_widget_available is True
    assert capabilities.osmand_extension_available is True
    assert capabilities.preferred_backend == "osmand_native"


def test_map_runtime_service_falls_back_to_legacy_when_osmand_extension_is_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(map_runtime_service_module, "_has_qt_application", lambda: True)
    monkeypatch.setattr(map_runtime_service_module, "check_opengl_support", lambda: True)
    monkeypatch.setattr(map_runtime_service_module, "prefer_osmand_native_widget", lambda: True)
    monkeypatch.setattr(map_runtime_service_module, "has_usable_osmand_native_widget", lambda root: False)
    monkeypatch.setattr(
        map_runtime_service_module,
        "choose_default_map_source",
        lambda root, **_kwargs: MapSourceSpec.legacy_default(root),
    )
    monkeypatch.setattr(
        map_runtime_service_module,
        "has_usable_osmand_search_extension",
        lambda package_root=None: True,
    )

    capabilities = SessionMapRuntimeService(tmp_path).capabilities()

    assert capabilities.preferred_backend == "legacy_python"
    assert capabilities.display_available is True
    assert capabilities.location_search_available is True


def test_map_runtime_service_checks_search_extension_against_bound_package_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    probed_roots: list[Path] = []

    monkeypatch.setattr(map_runtime_service_module, "_has_qt_application", lambda: True)
    monkeypatch.setattr(map_runtime_service_module, "check_opengl_support", lambda: True)
    monkeypatch.setattr(map_runtime_service_module, "prefer_osmand_native_widget", lambda: False)
    monkeypatch.setattr(map_runtime_service_module, "has_usable_osmand_native_widget", lambda root: False)
    monkeypatch.setattr(
        map_runtime_service_module,
        "choose_default_map_source",
        lambda root, **_kwargs: MapSourceSpec.osmand_default(root),
    )

    def _fake_has_usable_osmand_search_extension(package_root: Path | None = None) -> bool:
        assert package_root is not None
        probed_roots.append(package_root)
        return package_root == tmp_path

    monkeypatch.setattr(
        map_runtime_service_module,
        "has_usable_osmand_search_extension",
        _fake_has_usable_osmand_search_extension,
    )

    capabilities = SessionMapRuntimeService(tmp_path).capabilities()

    assert probed_roots == [tmp_path]
    assert capabilities.location_search_available is True


def test_native_failure_selects_legacy_without_disabling_search(monkeypatch, tmp_path):
    monkeypatch.setattr(map_runtime_service_module, "_has_qt_application", lambda: True)
    monkeypatch.setattr(map_runtime_service_module, "check_opengl_support", lambda: True)
    monkeypatch.setattr(map_runtime_service_module, "prefer_osmand_native_widget", lambda: True)
    monkeypatch.setattr(map_runtime_service_module, "has_usable_osmand_native_widget", lambda root: True)
    monkeypatch.setattr(map_runtime_service_module, "probe_native_widget_runtime", lambda root: (False, "incompatible surface"))
    monkeypatch.setattr(map_runtime_service_module, "choose_default_map_source",
                        lambda root, **kw: MapSourceSpec.osmand_default(root))
    monkeypatch.setattr(map_runtime_service_module, "has_usable_osmand_search_extension", lambda root: True)
    capabilities = SessionMapRuntimeService(tmp_path).capabilities()
    assert capabilities.preferred_backend == "legacy_python"
    assert capabilities.native_failure_reason == "incompatible surface"
    assert capabilities.location_search_available
    from iPhoto.gui.ui.widgets.map_widget_factory import choose_map_widget_backend
    _, source, backend = choose_map_widget_backend(
        MapSourceSpec.osmand_default(tmp_path), use_opengl=True,
        runtime_capabilities=capabilities, package_root=tmp_path,
    )
    assert backend == "legacy_python"
    assert source.kind == "legacy_pbf"
