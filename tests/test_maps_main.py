import os
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Signal
from PySide6.QtWidgets import QApplication, QWidget

from iPhoto.gui.i18n import TranslationManager, formatters
from iPhoto.gui.i18n import translation_manager as translation_manager_module
from iPhoto.settings.manager import SettingsManager
import maps.main as maps_main
from maps.main import (
    MainWindow,
    build_argument_parser,
    check_opengl_support,
    choose_default_map_source,
    choose_launch_configuration,
    choose_native_widget_class,
    configure_qt_opengl_defaults,
    describe_active_backend,
    format_map_runtime_diagnostics,
    format_status_message,
    prepare_qt_runtime_for_backend,
)
from maps.map_sources import MapBackendMetadata, MapSourceSpec


@pytest.fixture
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _settings(tmp_path: Path) -> SettingsManager:
    manager = SettingsManager(path=tmp_path / "settings.json")
    manager.load()
    return manager


def _reset_translation(app: QApplication) -> None:
    translator = translation_manager_module._INSTALLED_TRANSLATOR
    if translator is not None:
        app.removeTranslator(translator)
    translation_manager_module._INSTALLED_TRANSLATOR = None
    formatters.set_current_locale(None)


class _PreviewMapWidget(QWidget):
    viewChanged = Signal(float, float, float)

    def __init__(self, parent: QWidget | None = None, *, map_source: MapSourceSpec | None = None) -> None:
        super().__init__(parent)
        self._zoom = 4.25
        self._center = (12.3456, 48.8566)
        self._map_source = map_source

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float) -> None:
        self._zoom = float(zoom)
        longitude, latitude = self._center
        self.viewChanged.emit(longitude, latitude, self._zoom)

    def reset_view(self) -> None:
        self.set_zoom(2.0)

    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        del delta_x, delta_y

    def center_on(self, longitude: float, latitude: float) -> None:
        self._center = (float(longitude), float(latitude))
        self.viewChanged.emit(float(longitude), float(latitude), self._zoom)

    def center_lonlat(self) -> tuple[float, float]:
        return self._center

    def project_lonlat(self, longitude: float, latitude: float) -> QPointF:
        return QPointF(longitude, latitude)

    def map_backend_metadata(self) -> MapBackendMetadata:
        return MapBackendMetadata(2.0, 19.0, True, "raster", "xyz")

    def event_target(self) -> QWidget:
        return self

    def shutdown(self) -> None:
        return None


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


def test_choose_native_widget_class_still_uses_native_when_generic_opengl_probe_failed(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: root == package_root)
    monkeypatch.setattr("maps.main.probe_native_widget_runtime", lambda root: (True, None))
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)

    widget_cls, message = choose_native_widget_class(package_root, use_opengl=False)

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


def test_choose_native_widget_class_prefers_python_renderer_when_native_is_disabled(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    probe_calls: list[Path] = []

    monkeypatch.setattr("maps.main.prefer_osmand_native_widget", lambda: False)
    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: root == package_root)
    monkeypatch.setattr(
        "maps.main.probe_native_widget_runtime",
        lambda root: (probe_calls.append(root), (True, None))[1],
    )

    widget_cls, message = choose_native_widget_class(package_root, use_opengl=True)

    assert widget_cls is None
    assert "disabled by configuration" in message
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


@pytest.mark.parametrize("arguments", [["--bogus"], ["--center", "not-a-number", "51.2195"]])
def test_main_rejects_invalid_cli_before_qapplication(monkeypatch: pytest.MonkeyPatch, arguments: list[str]) -> None:
    prepare_calls: list[tuple[object, ...]] = []

    def fail_qapplication(*_args: object, **_kwargs: object) -> None:
        pytest.fail("QApplication should not be constructed for invalid CLI arguments")

    monkeypatch.setattr(
        maps_main,
        "prepare_qt_runtime_for_backend",
        lambda *args: prepare_calls.append(args),
    )
    monkeypatch.setattr(maps_main, "QApplication", fail_qapplication)

    with pytest.raises(SystemExit) as exc_info:
        maps_main.main(arguments)

    assert exc_info.value.code == 2
    assert prepare_calls == []


def test_build_argument_parser_uses_installed_translations(tmp_path: Path, qapp: QApplication) -> None:
    settings = _settings(tmp_path)
    translations = TranslationManager(settings)
    try:
        translations.apply_language("zh-CN")

        help_text = build_argument_parser().format_help()

        assert "预览 OsmAnd 或旧版地图后端" in help_text
        assert "选择启动渲染器" in help_text
    finally:
        _reset_translation(qapp)


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
    monkeypatch.setattr("maps.main.sys.platform", "linux")
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)

    assert check_opengl_support() is True


def test_check_opengl_support_requires_make_current_on_macos(monkeypatch) -> None:
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
    monkeypatch.setattr("maps.main.sys.platform", "darwin")
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)

    assert check_opengl_support() is False


def test_configure_qt_opengl_defaults_prefers_desktop_opengl(monkeypatch) -> None:
    helper_calls: list[bool] = []
    attributes: list[tuple[object, bool]] = []
    default_formats: list[object] = []

    monkeypatch.setattr("maps.main.configure_shader_cache_environment", lambda: helper_calls.append(True))
    monkeypatch.setattr("maps.main.QApplication.setAttribute", lambda attr, enabled=True: attributes.append((attr, enabled)))
    monkeypatch.setattr("maps.main.QSurfaceFormat.setDefaultFormat", lambda fmt: default_formats.append(fmt))
    monkeypatch.setattr("maps.main.sys.platform", "linux")
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)

    configure_qt_opengl_defaults()

    assert helper_calls == [True]
    assert len(attributes) == 2
    assert all(enabled is True for _, enabled in attributes)
    assert len(default_formats) == 1
    assert default_formats[0].depthBufferSize() == 24
    assert default_formats[0].stencilBufferSize() == 8
    assert default_formats[0].alphaBufferSize() == 0
    assert default_formats[0].samples() == 0


def test_configure_qt_opengl_defaults_requests_alpha_on_macos(monkeypatch) -> None:
    default_formats: list[object] = []

    monkeypatch.setattr("maps.main.configure_shader_cache_environment", lambda: None)
    monkeypatch.setattr("maps.main.QApplication.setAttribute", lambda _attr, enabled=True: None)
    monkeypatch.setattr("maps.main.QSurfaceFormat.setDefaultFormat", lambda fmt: default_formats.append(fmt))
    monkeypatch.setattr("maps.main.sys.platform", "darwin")
    monkeypatch.delenv("IPHOTO_DISABLE_OPENGL", raising=False)

    configure_qt_opengl_defaults()

    assert len(default_formats) == 1
    assert default_formats[0].depthBufferSize() == 24
    assert default_formats[0].stencilBufferSize() == 8
    assert default_formats[0].alphaBufferSize() == 8
    assert default_formats[0].samples() == 0


def test_configure_qt_opengl_defaults_still_routes_shader_cache_when_opengl_is_disabled(monkeypatch) -> None:
    helper_calls: list[bool] = []
    attributes: list[tuple[object, bool]] = []

    monkeypatch.setattr("maps.main.configure_shader_cache_environment", lambda: helper_calls.append(True))
    monkeypatch.setattr("maps.main.QApplication.setAttribute", lambda attr, enabled=True: attributes.append((attr, enabled)))
    monkeypatch.setattr("maps.main.QSurfaceFormat.setDefaultFormat", lambda _fmt: None)
    monkeypatch.setenv("IPHOTO_DISABLE_OPENGL", "1")

    configure_qt_opengl_defaults()

    assert helper_calls == [True]
    assert attributes == []


def test_prepare_qt_runtime_for_backend_forces_xcb_glx_on_linux(monkeypatch) -> None:
    attributes: list[tuple[object, bool]] = []

    monkeypatch.setattr("maps.main.sys.platform", "linux")
    monkeypatch.setattr("maps.main._is_packaged_runtime", lambda: False)
    monkeypatch.setattr("maps.main.QApplication.setAttribute", lambda attr, enabled=True: attributes.append((attr, enabled)))
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_OPENGL", raising=False)
    monkeypatch.delenv("QT_XCB_GL_INTEGRATION", raising=False)

    prepare_qt_runtime_for_backend("auto")

    assert os.environ["QT_QPA_PLATFORM"] == "xcb"
    assert os.environ["QT_OPENGL"] == "desktop"
    assert os.environ["QT_XCB_GL_INTEGRATION"] == "xcb_glx"
    assert len(attributes) == 1


def test_prepare_qt_runtime_for_backend_skips_linux_override_for_python_backend(monkeypatch) -> None:
    attributes: list[tuple[object, bool]] = []

    monkeypatch.setattr("maps.main.sys.platform", "linux")
    monkeypatch.setattr("maps.main._is_packaged_runtime", lambda: False)
    monkeypatch.setattr("maps.main.QApplication.setAttribute", lambda attr, enabled=True: attributes.append((attr, enabled)))
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_OPENGL", raising=False)
    monkeypatch.delenv("QT_XCB_GL_INTEGRATION", raising=False)

    prepare_qt_runtime_for_backend("python")

    assert "QT_QPA_PLATFORM" not in os.environ
    assert "QT_OPENGL" not in os.environ
    assert "QT_XCB_GL_INTEGRATION" not in os.environ
    assert attributes == []


def test_prepare_qt_runtime_for_backend_forces_xcb_glx_in_packaged_linux_builds(monkeypatch) -> None:
    attributes: list[tuple[object, bool]] = []

    monkeypatch.setattr("maps.main.sys.platform", "linux")
    monkeypatch.setattr("maps.main._is_packaged_runtime", lambda: True)
    monkeypatch.setattr("maps.main.QApplication.setAttribute", lambda attr, enabled=True: attributes.append((attr, enabled)))
    monkeypatch.delenv("IPHOTO_ALLOW_PACKAGED_LINUX_WAYLAND", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_OPENGL", raising=False)
    monkeypatch.delenv("QT_XCB_GL_INTEGRATION", raising=False)

    prepare_qt_runtime_for_backend("auto")

    assert os.environ["QT_QPA_PLATFORM"] == "xcb"
    assert os.environ["QT_OPENGL"] == "desktop"
    assert os.environ["QT_XCB_GL_INTEGRATION"] == "xcb_glx"
    assert len(attributes) == 1


def test_prepare_qt_runtime_for_backend_allows_packaged_linux_wayland_opt_out(monkeypatch) -> None:
    attributes: list[tuple[object, bool]] = []

    monkeypatch.setattr("maps.main.sys.platform", "linux")
    monkeypatch.setattr("maps.main._is_packaged_runtime", lambda: True)
    monkeypatch.setattr("maps.main.QApplication.setAttribute", lambda attr, enabled=True: attributes.append((attr, enabled)))
    monkeypatch.setenv("IPHOTO_ALLOW_PACKAGED_LINUX_WAYLAND", "1")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_OPENGL", raising=False)
    monkeypatch.delenv("QT_XCB_GL_INTEGRATION", raising=False)

    prepare_qt_runtime_for_backend("auto")

    assert "QT_QPA_PLATFORM" not in os.environ
    assert "QT_OPENGL" not in os.environ
    assert "QT_XCB_GL_INTEGRATION" not in os.environ
    assert attributes == []


def test_prepare_qt_runtime_for_backend_allows_packaged_linux_wayland_opt_out_for_native_backend(monkeypatch) -> None:
    attributes: list[tuple[object, bool]] = []

    monkeypatch.setattr("maps.main.sys.platform", "linux")
    monkeypatch.setattr("maps.main._is_packaged_runtime", lambda: True)
    monkeypatch.setattr("maps.main.QApplication.setAttribute", lambda attr, enabled=True: attributes.append((attr, enabled)))
    monkeypatch.setenv("IPHOTO_ALLOW_PACKAGED_LINUX_WAYLAND", "1")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_OPENGL", raising=False)
    monkeypatch.delenv("QT_XCB_GL_INTEGRATION", raising=False)

    prepare_qt_runtime_for_backend("native")

    assert "QT_QPA_PLATFORM" not in os.environ
    assert "QT_OPENGL" not in os.environ
    assert "QT_XCB_GL_INTEGRATION" not in os.environ
    assert attributes == []


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


def test_choose_launch_configuration_auto_prefers_python_obf_when_native_is_disabled(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"

    monkeypatch.setattr("maps.main.prefer_osmand_native_widget", lambda: False)
    monkeypatch.setattr("maps.main.has_usable_osmand_native_widget", lambda root: root == package_root)
    monkeypatch.setattr("maps.main.probe_native_widget_runtime", lambda root: (True, None))
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
    assert "disabled by configuration" in launch_config.startup_message


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
    assert r"native_library=D:\native\osmand_native_widget.dll" in diagnostics


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


def test_map_preview_window_retranslates_actions_and_status(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    settings = _settings(tmp_path)
    translations = TranslationManager(settings)
    window: MainWindow | None = None
    try:
        translations.apply_language("zh-CN")
        source = MapSourceSpec(kind="osmand_obf", data_path=r"D:\maps\World_basemap_2.obf")

        window = MainWindow(
            map_source=source,
            widget_class=_PreviewMapWidget,
            native_widget_class=None,
        )
        window.retranslate_ui()

        assert window._file_menu.title() == "文件"
        assert window._view_menu.title() == "视图"
        assert window._navigate_menu.title() == "导航"
        assert window._action_zoom_in.text() == "放大"
        assert window._action_open_map_source.text() == "选择地图源..."
        assert window.windowTitle().startswith("地图预览 - OBF 栅格 - 缩放 4.25")
        assert "缩放 4.25" in window.statusBar().currentMessage()
        assert "中心 48.8566, 12.3456" in window.statusBar().currentMessage()
        assert "来源 World_basemap_2.obf" in window.statusBar().currentMessage()
    finally:
        if window is not None:
            window.close()
        _reset_translation(qapp)
