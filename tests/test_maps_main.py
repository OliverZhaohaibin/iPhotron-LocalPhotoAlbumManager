from pathlib import Path

from maps.main import (
    build_argument_parser,
    check_opengl_support,
    configure_qt_opengl_defaults,
    choose_default_map_source,
    choose_launch_configuration,
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
    assert Path(source.data_path) == package_root / "tiles" / "extension" / "World_basemap_2.obf"


def test_choose_default_map_source_prefers_obf_when_helper_is_usable(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: False)
    monkeypatch.setattr("maps.main.has_usable_osmand_default", lambda root: root == package_root)

    source = choose_default_map_source(package_root, use_opengl=True)

    assert source.kind == "osmand_obf"
    assert Path(source.data_path) == package_root / "tiles" / "extension" / "World_basemap_2.obf"


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


def test_build_argument_parser_supports_debug_capture_flags() -> None:
    parser = build_argument_parser()

    parsed = parser.parse_args(
        [
            "--backend",
            "native",
            "--center",
            "9.5683",
            "51.2195",
            "--zoom",
            "7.47",
            "--screenshot",
            "debug/native.png",
            "--capture-delay-ms",
            "2500",
        ]
    )

    assert parsed.backend == "native"
    assert parsed.center == [9.5683, 51.2195]
    assert parsed.zoom == 7.47
    assert parsed.screenshot == Path("debug/native.png")
    assert parsed.capture_delay_ms == 2500


def test_check_opengl_support_accepts_valid_context_when_offscreen_make_current_fails(monkeypatch) -> None:
    class FakeSurface:
        def create(self) -> None:
            return None

        def isValid(self) -> bool:
            return True

    class FakeContext:
        def create(self) -> bool:
            return True

        def isValid(self) -> bool:
            return True

        def makeCurrent(self, surface) -> bool:
            del surface
            return False

        def doneCurrent(self) -> None:
            return None

    monkeypatch.setattr("maps.main.QOffscreenSurface", lambda: FakeSurface())
    monkeypatch.setattr("maps.main.QOpenGLContext", lambda: FakeContext())
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)

    assert check_opengl_support() is True


def test_configure_qt_opengl_defaults_prefers_desktop_opengl(monkeypatch) -> None:
    attributes: list[tuple[object, bool]] = []
    default_formats: list[object] = []

    monkeypatch.setattr("maps.main.QApplication.setAttribute", lambda attr, enabled=True: attributes.append((attr, enabled)))
    monkeypatch.setattr("maps.main.QSurfaceFormat.setDefaultFormat", lambda fmt: default_formats.append(fmt))
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)

    configure_qt_opengl_defaults()

    assert len(attributes) == 2
    assert all(enabled is True for _, enabled in attributes)
    assert len(default_formats) == 1


def test_choose_launch_configuration_can_force_native_backend(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: root == package_root)
    monkeypatch.setattr("maps.main.probe_native_widget_runtime", lambda root: (True, None))

    launch_config = choose_launch_configuration(
        package_root,
        use_opengl=True,
        backend="native",
    )

    assert launch_config.map_source.kind == "osmand_obf"
    assert Path(launch_config.map_source.data_path) == package_root / "tiles" / "extension" / "World_basemap_2.obf"
    assert launch_config.native_widget_class is not None
    assert "native OsmAnd widget" in launch_config.startup_message


def test_choose_launch_configuration_can_force_python_obf_backend(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    monkeypatch.setattr("maps.main.has_usable_osmand_default", lambda root: root == package_root)
    monkeypatch.setattr("maps.main.probe_python_obf_runtime", lambda root: (True, None))

    launch_config = choose_launch_configuration(
        package_root,
        use_opengl=True,
        backend="python",
    )

    assert launch_config.map_source.kind == "osmand_obf"
    assert launch_config.native_widget_class is None
    assert "Python OBF renderer" in launch_config.startup_message


def test_choose_launch_configuration_auto_prefers_native_widget_when_runtime_is_available(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"

    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: root == package_root)
    monkeypatch.setattr("maps.main.probe_native_widget_runtime", lambda root: (True, None))
    monkeypatch.setattr("maps.main.has_usable_osmand_default", lambda root: root == package_root)

    launch_config = choose_launch_configuration(
        package_root,
        use_opengl=True,
        backend="auto",
    )

    assert launch_config.map_source.kind == "osmand_obf"
    assert launch_config.native_widget_class is not None
    assert "native OsmAnd widget" in launch_config.startup_message


def test_choose_launch_configuration_auto_prefers_python_obf_when_native_is_unavailable(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"

    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: False)
    monkeypatch.setattr("maps.main.has_usable_osmand_default", lambda root: root == package_root)
    monkeypatch.setattr("maps.main.probe_python_obf_runtime", lambda root: (True, None))

    launch_config = choose_launch_configuration(
        package_root,
        use_opengl=True,
        backend="auto",
    )

    assert launch_config.map_source.kind == "osmand_obf"
    assert launch_config.native_widget_class is None
    assert "Python OBF renderer" in launch_config.startup_message


def test_choose_launch_configuration_auto_falls_back_to_legacy_when_helper_runtime_probe_fails(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "maps"
    monkeypatch.setattr("maps.main.has_usable_osmand_default", lambda root: root == package_root)
    monkeypatch.setattr(
        "maps.main.probe_python_obf_runtime",
        lambda root: (False, "TileBackendUnavailableError: Timed out while waiting for the OsmAnd helper"),
    )

    launch_config = choose_launch_configuration(
        package_root,
        use_opengl=True,
        backend="auto",
    )

    assert launch_config.map_source.kind == "legacy_pbf"
    assert launch_config.native_widget_class is None
    assert "legacy vector renderer" in launch_config.startup_message
    assert "Timed out while waiting for the OsmAnd helper" in launch_config.startup_message


def test_choose_launch_configuration_can_force_legacy_backend(tmp_path) -> None:
    package_root = tmp_path / "maps"

    launch_config = choose_launch_configuration(
        package_root,
        use_opengl=False,
        backend="legacy",
    )

    assert launch_config.map_source.kind == "legacy_pbf"
    assert Path(launch_config.map_source.data_path) == package_root / "tiles"
    assert launch_config.native_widget_class is None
    assert "legacy vector renderer" in launch_config.startup_message


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
