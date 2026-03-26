from pathlib import Path

from maps.main import (
    choose_default_map_source,
    choose_native_widget_class,
    describe_active_backend,
    format_map_runtime_diagnostics,
    format_status_message,
)
from maps.map_sources import MapBackendMetadata, MapSourceSpec


def test_choose_default_map_source_prefers_obf_when_native_runtime_is_usable_without_helper(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "maps"
    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: root == package_root)
    monkeypatch.setattr("maps.main.has_usable_osmand_default", lambda root: False)
    monkeypatch.setattr("maps.main.probe_native_widget_runtime", lambda root: (True, None))

    source = choose_default_map_source(package_root, use_opengl=True)

    assert source.kind == "osmand_obf"
    assert Path(source.data_path) == package_root / "tiles" / "World_basemap_2.obf"


def test_choose_default_map_source_prefers_obf_when_helper_is_usable(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: False)
    monkeypatch.setattr("maps.main.has_usable_osmand_default", lambda root: root == package_root)

    source = choose_default_map_source(package_root, use_opengl=True)

    assert source.kind == "osmand_obf"
    assert Path(source.data_path) == package_root / "tiles" / "World_basemap_2.obf"


def test_choose_default_map_source_falls_back_to_legacy_when_native_runtime_probe_fails_without_helper(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "maps"
    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: root == package_root)
    monkeypatch.setattr("maps.main.has_usable_osmand_default", lambda root: False)
    monkeypatch.setattr("maps.main.probe_native_widget_runtime", lambda root: (False, "missing runtime"))

    source = choose_default_map_source(package_root, use_opengl=True)

    assert source.kind == "legacy_pbf"
    assert Path(source.data_path) == package_root / "tiles"


def test_choose_default_map_source_falls_back_to_legacy_without_native_or_helper(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: False)
    monkeypatch.setattr("maps.main.has_usable_osmand_default", lambda root: False)

    source = choose_default_map_source(package_root, use_opengl=True)

    assert source.kind == "legacy_pbf"
    assert Path(source.data_path) == package_root / "tiles"


def test_choose_default_map_source_falls_back_to_legacy_when_only_native_is_available_without_opengl(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "maps"
    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: root == package_root)
    monkeypatch.setattr("maps.main.has_usable_osmand_default", lambda root: False)

    source = choose_default_map_source(package_root, use_opengl=False)

    assert source.kind == "legacy_pbf"
    assert Path(source.data_path) == package_root / "tiles"


def test_choose_native_widget_class_uses_native_only_when_runtime_probe_succeeds(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: root == package_root)
    monkeypatch.setattr("maps.main.probe_native_widget_runtime", lambda root: (True, None))

    widget_cls, message = choose_native_widget_class(package_root, use_opengl=True)

    assert widget_cls is not None
    assert "native OsmAnd widget" in message


def test_choose_native_widget_class_falls_back_when_runtime_probe_fails(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: root == package_root)
    monkeypatch.setattr(
        "maps.main.probe_native_widget_runtime",
        lambda root: (False, "OSError: [WinError 127] The specified procedure could not be found"),
    )

    widget_cls, message = choose_native_widget_class(package_root, use_opengl=True)

    assert widget_cls is None
    assert "WinError 127" in message


def test_choose_native_widget_class_can_force_python_renderer(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    probe_calls: list[Path] = []

    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: root == package_root)
    monkeypatch.setattr(
        "maps.main.probe_native_widget_runtime",
        lambda root: (probe_calls.append(root), (True, None))[1],
    )

    widget_cls, message = choose_native_widget_class(
        package_root,
        use_opengl=True,
        prefer_native_widget=False,
    )

    assert widget_cls is None
    assert "Location section" in message
    assert probe_calls == []


def test_format_map_runtime_diagnostics_reports_native_gl(monkeypatch) -> None:
    class FakeEventTarget:
        def objectName(self) -> str:
            return "NativeOsmAndMapWidget"

    class FakeNativeWidget:
        def map_backend_metadata(self) -> MapBackendMetadata:
            return MapBackendMetadata(2.0, 19.0, True, "raster", "xyz")

        def event_target(self) -> FakeEventTarget:
            return FakeEventTarget()

        def loaded_library_path(self) -> Path:
            return Path(r"D:\native\osmand_native_widget.dll")

    monkeypatch.setattr("maps.main.NativeOsmAndWidget", FakeNativeWidget)

    diagnostics = format_map_runtime_diagnostics(
        FakeNativeWidget(),
        map_source=MapSourceSpec(kind="osmand_obf", data_path="world.obf"),
    )

    assert diagnostics.startswith("[maps.main] ")
    assert "backend=osmand_native" in diagnostics
    assert "confirmed_gl=true" in diagnostics
    assert "event_target=NativeOsmAndMapWidget" in diagnostics
    assert "tile_kind=raster" in diagnostics
    assert r"native_dll=D:\native\osmand_native_widget.dll" in diagnostics


def test_describe_active_backend_distinguishes_helper_and_fallback() -> None:
    raster_metadata = MapBackendMetadata(2.0, 19.0, False, "raster")
    vector_metadata = MapBackendMetadata(0.0, 6.0, False, "vector")

    assert (
        describe_active_backend(
            MapSourceSpec(kind="osmand_obf", data_path="world.obf"),
            raster_metadata,
        )
        == "OBF Raster"
    )
    assert (
        describe_active_backend(
            MapSourceSpec(kind="osmand_obf", data_path="world.obf"),
            vector_metadata,
        )
        == "Legacy Vector Fallback"
    )
    assert (
        describe_active_backend(
            MapSourceSpec(kind="legacy_pbf", data_path="tiles"),
            vector_metadata,
        )
        == "Legacy Vector"
    )


def test_format_status_message_includes_backend_zoom_and_center() -> None:
    source = MapSourceSpec(kind="osmand_obf", data_path=r"D:\maps\World_basemap_2.obf")
    metadata = MapBackendMetadata(2.0, 19.0, False, "raster")

    message = format_status_message(
        source,
        metadata,
        zoom=4.25,
        longitude=12.3456,
        latitude=48.8566,
    )

    assert "OBF Raster" in message
    assert "Zoom 4.25" in message
    assert "48.8566, 12.3456" in message
    assert "World_basemap_2.obf" in message
