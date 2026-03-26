from pathlib import Path

from maps import map_sources
from maps.map_sources import (
    MapSourceSpec,
    has_usable_osmand_default,
    resolve_osmand_native_widget_library,
    resolve_osmand_helper_command,
)


def test_default_map_source_prefers_osmand_when_assets_exist(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    tiles_dir = package_root / "tiles"
    tiles_dir.mkdir(parents=True)
    (tiles_dir / "World_basemap_2.obf").write_bytes(b"obf")
    (tiles_dir / "style.json").write_text("{}", encoding="utf-8")

    resources_root = tmp_path / "OsmAnd-resources"
    style_path = resources_root / "rendering_styles" / "snowmobile.render.xml"
    style_path.parent.mkdir(parents=True)
    style_path.write_text("<renderingStyle />", encoding="utf-8")

    monkeypatch.setattr(map_sources, "DEFAULT_OSMAND_RESOURCES_ROOT", resources_root)
    monkeypatch.setattr(map_sources, "DEFAULT_OSMAND_STYLE_PATH", style_path)

    source = MapSourceSpec.default(package_root)

    assert source.kind == "osmand_obf"
    assert Path(source.data_path) == tiles_dir / "World_basemap_2.obf"
    assert Path(source.resources_root) == resources_root
    assert Path(source.style_path) == style_path


def test_default_map_source_falls_back_to_legacy_without_obf(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    tiles_dir = package_root / "tiles"
    tiles_dir.mkdir(parents=True)
    (tiles_dir / "style.json").write_text("{}", encoding="utf-8")

    resources_root = tmp_path / "OsmAnd-resources"
    style_path = resources_root / "rendering_styles" / "snowmobile.render.xml"
    style_path.parent.mkdir(parents=True)
    style_path.write_text("<renderingStyle />", encoding="utf-8")

    monkeypatch.setattr(map_sources, "DEFAULT_OSMAND_RESOURCES_ROOT", resources_root)
    monkeypatch.setattr(map_sources, "DEFAULT_OSMAND_STYLE_PATH", style_path)

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


def test_resolve_osmand_helper_command_discovers_built_helper(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "src" / "maps"
    package_root.mkdir(parents=True)
    helper_path = tmp_path / "tools" / "osmand_render_helper_native" / "dist" / "osmand_render_helper.exe"
    helper_path.parent.mkdir(parents=True)
    helper_path.write_bytes(b"exe")
    monkeypatch.delenv(map_sources.ENV_OSMAND_HELPER, raising=False)

    command = resolve_osmand_helper_command(package_root)

    assert command == (str(helper_path.resolve()),)


def test_resolve_osmand_helper_command_discovers_official_build_output(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "src" / "maps"
    package_root.mkdir(parents=True)
    helper_path = (
        tmp_path
        / "official"
        / "binaries"
        / "windows"
        / "gcc-amd64"
        / "amd64"
        / "Release"
        / "osmand_render_helper.exe"
    )
    helper_path.parent.mkdir(parents=True)
    helper_path.write_bytes(b"exe")
    monkeypatch.delenv(map_sources.ENV_OSMAND_HELPER, raising=False)
    monkeypatch.setattr(map_sources, "DEFAULT_OFFICIAL_OSMAND_ROOT", tmp_path / "official")

    command = resolve_osmand_helper_command(package_root)

    assert command == (str(helper_path.resolve()),)


def test_resolve_osmand_native_widget_library_prefers_official_release_output(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "src" / "maps"
    package_root.mkdir(parents=True)
    local_dll = tmp_path / "tools" / "osmand_render_helper_native" / "dist-msvc" / "osmand_native_widget.dll"
    local_dll.parent.mkdir(parents=True)
    local_dll.write_bytes(b"old")
    official_dll = (
        tmp_path
        / "official"
        / "binaries"
        / "windows"
        / "msvc-amd64"
        / "Release"
        / "osmand_native_widget.dll"
    )
    official_dll.parent.mkdir(parents=True)
    official_dll.write_bytes(b"new")

    monkeypatch.delenv(map_sources.ENV_OSMAND_NATIVE_WIDGET_LIBRARY, raising=False)
    monkeypatch.setattr(map_sources, "DEFAULT_OFFICIAL_OSMAND_ROOT", tmp_path / "official")

    resolved = resolve_osmand_native_widget_library(package_root)

    assert resolved == official_dll.resolve()


def test_has_usable_osmand_default_requires_helper(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    tiles_dir = package_root / "tiles"
    tiles_dir.mkdir(parents=True)
    (tiles_dir / "World_basemap_2.obf").write_bytes(b"obf")

    resources_root = tmp_path / "OsmAnd-resources"
    style_path = resources_root / "rendering_styles" / "snowmobile.render.xml"
    style_path.parent.mkdir(parents=True)
    style_path.write_text("<renderingStyle />", encoding="utf-8")

    monkeypatch.setattr(map_sources, "DEFAULT_OSMAND_RESOURCES_ROOT", resources_root)
    monkeypatch.setattr(map_sources, "DEFAULT_OSMAND_STYLE_PATH", style_path)
    monkeypatch.setattr(map_sources, "DEFAULT_OFFICIAL_OSMAND_ROOT", tmp_path / "official")
    monkeypatch.delenv(map_sources.ENV_OSMAND_HELPER, raising=False)

    assert has_usable_osmand_default(package_root) is False

    helper_path = (
        tmp_path
        / "official"
        / "binaries"
        / "windows"
        / "gcc-amd64"
        / "amd64"
        / "Release"
        / "osmand_render_helper.exe"
    )
    helper_path.parent.mkdir(parents=True)
    helper_path.write_bytes(b"exe")

    assert has_usable_osmand_default(package_root) is True
