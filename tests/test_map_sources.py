from pathlib import Path

from maps import map_sources
from maps.map_sources import (
    DEFAULT_HELPER_RELATIVE_PATHS,
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATHS,
    MapSourceSpec,
    default_osmand_extension_root,
    has_usable_osmand_default,
    resolve_osmand_native_widget_library,
    resolve_osmand_helper_command,
)


def _create_extension_assets(package_root: Path) -> Path:
    extension_root = default_osmand_extension_root(package_root)
    (extension_root / "rendering_styles").mkdir(parents=True, exist_ok=True)
    (extension_root / "poi").mkdir(parents=True, exist_ok=True)
    (extension_root / "routing").mkdir(parents=True, exist_ok=True)
    (extension_root / "misc" / "icu4c").mkdir(parents=True, exist_ok=True)
    (extension_root / "World_basemap_2.obf").write_bytes(b"obf")
    (extension_root / "rendering_styles" / "snowmobile.render.xml").write_text(
        "<renderingStyle />",
        encoding="utf-8",
    )
    return extension_root


def test_default_map_source_prefers_osmand_when_assets_exist(tmp_path) -> None:
    package_root = tmp_path / "maps"
    tiles_dir = package_root / "tiles"
    tiles_dir.mkdir(parents=True)
    (tiles_dir / "style.json").write_text("{}", encoding="utf-8")
    extension_root = _create_extension_assets(package_root)

    source = MapSourceSpec.default(package_root)

    assert source.kind == "osmand_obf"
    assert Path(source.data_path) == extension_root / "World_basemap_2.obf"
    assert Path(source.resources_root) == extension_root
    assert Path(source.style_path) == extension_root / "rendering_styles" / "snowmobile.render.xml"


def test_default_map_source_falls_back_to_legacy_without_obf(tmp_path) -> None:
    package_root = tmp_path / "maps"
    tiles_dir = package_root / "tiles"
    tiles_dir.mkdir(parents=True)
    (tiles_dir / "style.json").write_text("{}", encoding="utf-8")
    extension_root = default_osmand_extension_root(package_root)
    (extension_root / "rendering_styles").mkdir(parents=True, exist_ok=True)
    (extension_root / "rendering_styles" / "snowmobile.render.xml").write_text(
        "<renderingStyle />",
        encoding="utf-8",
    )

    source = MapSourceSpec.default(package_root)

    assert source.kind == "legacy_pbf"
    assert Path(source.data_path) == tiles_dir
    assert Path(source.style_path) == package_root / "style.json"


def test_resolve_osmand_helper_command_prefers_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        map_sources.ENV_OSMAND_HELPER,
        r'"D:\helper path\osmand_render_helper.exe" --flag',
    )

    command = resolve_osmand_helper_command()

    assert command == (r'"D:\helper path\osmand_render_helper.exe"', "--flag")


def test_resolve_osmand_helper_command_discovers_extension_helper(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "src" / "maps"
    package_root.mkdir(parents=True)
    helper_path = package_root / DEFAULT_HELPER_RELATIVE_PATHS[0]
    helper_path.parent.mkdir(parents=True)
    helper_path.write_bytes(b"exe")
    monkeypatch.delenv(map_sources.ENV_OSMAND_HELPER, raising=False)

    command = resolve_osmand_helper_command(package_root)

    assert command == (str(helper_path.resolve()),)


def test_resolve_osmand_native_widget_library_prefers_extension_bin_output(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "src" / "maps"
    package_root.mkdir(parents=True)
    local_dll = package_root / DEFAULT_NATIVE_WIDGET_RELATIVE_PATHS[0]
    local_dll.parent.mkdir(parents=True)
    local_dll.write_bytes(b"dll")
    monkeypatch.delenv(map_sources.ENV_OSMAND_NATIVE_WIDGET_LIBRARY, raising=False)

    resolved = resolve_osmand_native_widget_library(package_root)

    assert resolved == local_dll.resolve()


def test_has_usable_osmand_default_requires_helper(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    tiles_dir = package_root / "tiles"
    tiles_dir.mkdir(parents=True)
    extension_root = _create_extension_assets(package_root)
    monkeypatch.delenv(map_sources.ENV_OSMAND_HELPER, raising=False)

    assert has_usable_osmand_default(package_root) is False

    helper_path = package_root / DEFAULT_HELPER_RELATIVE_PATHS[0]
    helper_path.parent.mkdir(parents=True)
    helper_path.write_bytes(b"exe")

    assert has_usable_osmand_default(package_root) is True
