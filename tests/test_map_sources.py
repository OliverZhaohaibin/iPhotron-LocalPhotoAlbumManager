from pathlib import Path

from maps import map_sources
from maps.map_sources import (
    DEFAULT_HELPER_RELATIVE_PATHS,
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATHS,
    ENV_OSMAND_EXTENSION_ROOT,
    MapSourceSpec,
    apply_pending_osmand_extension_install,
    default_osmand_extension_root,
    default_osmand_download_url,
    default_osmand_search_database,
    default_osmand_tiles_root,
    default_pending_osmand_extension_root,
    has_usable_osmand_default,
    has_installed_osmand_extension,
    resolve_osmand_native_widget_library,
    resolve_osmand_helper_command,
)


def _create_extension_assets(package_root: Path) -> Path:
    extension_root = default_osmand_extension_root(package_root)
    (extension_root / "rendering_styles").mkdir(parents=True, exist_ok=True)
    (extension_root / "search").mkdir(parents=True, exist_ok=True)
    (extension_root / "poi").mkdir(parents=True, exist_ok=True)
    (extension_root / "routing").mkdir(parents=True, exist_ok=True)
    (extension_root / "misc" / "icu4c").mkdir(parents=True, exist_ok=True)
    (extension_root / "bin").mkdir(parents=True, exist_ok=True)
    (extension_root / "World_basemap_2.obf").write_bytes(b"obf")
    (extension_root / "rendering_styles" / "snowmobile.render.xml").write_text(
        "<renderingStyle />",
        encoding="utf-8",
    )
    (extension_root / "search" / "geonames.sqlite3").write_bytes(b"sqlite")
    (extension_root / DEFAULT_HELPER_RELATIVE_PATHS[0].relative_to(Path("tiles") / "extension")).write_bytes(
        b"helper"
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


def test_resolve_osmand_helper_command_prefers_external_runtime_root_for_appimage(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "AppDir" / "opt" / "iPhotron" / "maps"
    package_root.mkdir(parents=True)
    external_data_home = tmp_path / "xdg-data"
    helper_path = (
        external_data_home
        / "iPhoto"
        / "maps"
        / "tiles"
        / "extension"
        / "bin"
        / DEFAULT_HELPER_RELATIVE_PATHS[0].name
    )
    helper_path.parent.mkdir(parents=True, exist_ok=True)
    helper_path.write_bytes(b"exe")
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "iPhotron.AppImage"))
    monkeypatch.setenv("XDG_DATA_HOME", str(external_data_home))
    monkeypatch.delenv(map_sources.ENV_OSMAND_HELPER, raising=False)
    monkeypatch.delenv(ENV_OSMAND_EXTENSION_ROOT, raising=False)

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
    helper_path = extension_root / DEFAULT_HELPER_RELATIVE_PATHS[0].relative_to(Path("tiles") / "extension")
    helper_path.unlink()
    monkeypatch.delenv(map_sources.ENV_OSMAND_HELPER, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty-data-home"))
    monkeypatch.delenv(ENV_OSMAND_EXTENSION_ROOT, raising=False)

    assert has_usable_osmand_default(package_root) is False

    helper_path = package_root / DEFAULT_HELPER_RELATIVE_PATHS[0]
    helper_path.parent.mkdir(parents=True, exist_ok=True)
    helper_path.write_bytes(b"exe")

    assert has_usable_osmand_default(package_root) is True


def test_default_osmand_download_url_matches_platform_variants() -> None:
    assert default_osmand_download_url("linux") == (
        "https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/"
        "releases/download/v5.0.0/extension.tar.xz"
    )
    assert default_osmand_download_url("win32") == (
        "https://github.com/OliverZhaohaibin/iPhotron-LocalPhotoAlbumManager/"
        "releases/download/v5.0.0/extension.zip"
    )
    assert default_osmand_download_url("darwin") is None


def test_has_installed_osmand_extension_requires_search_database_and_helper(tmp_path) -> None:
    package_root = tmp_path / "maps"
    _create_extension_assets(package_root)

    assert has_installed_osmand_extension(package_root) is True

    search_db = default_osmand_extension_root(package_root) / "search" / "geonames.sqlite3"
    search_db.unlink()
    assert has_installed_osmand_extension(package_root) is False


def test_default_osmand_extension_root_uses_external_runtime_path_for_appimage(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "AppDir" / "opt" / "iPhotron" / "maps"
    package_root.mkdir(parents=True)
    external_data_home = tmp_path / "xdg-data"
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "iPhotron.AppImage"))
    monkeypatch.setenv("XDG_DATA_HOME", str(external_data_home))
    monkeypatch.delenv(ENV_OSMAND_EXTENSION_ROOT, raising=False)

    extension_root = default_osmand_extension_root(package_root)

    assert extension_root == (
        external_data_home / "iPhoto" / "maps" / "tiles" / "extension"
    ).resolve()
    assert default_osmand_tiles_root(package_root) == extension_root.parent
    assert default_osmand_search_database(package_root) == extension_root / "search" / "geonames.sqlite3"


def test_default_osmand_extension_root_prefers_override_env(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "maps"
    override_root = tmp_path / "runtime" / "extension"
    monkeypatch.setenv(ENV_OSMAND_EXTENSION_ROOT, str(override_root))

    assert default_osmand_extension_root(package_root) == override_root.resolve()


def test_default_pending_osmand_extension_root_uses_external_runtime_path_for_appimage_when_bundled_exists(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "AppDir" / "opt" / "iPhotron" / "maps"
    bundled_root = package_root / "tiles" / "extension"
    bundled_root.mkdir(parents=True, exist_ok=True)
    external_data_home = tmp_path / "xdg-data"
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "iPhotron.AppImage"))
    monkeypatch.setenv("XDG_DATA_HOME", str(external_data_home))
    monkeypatch.delenv(ENV_OSMAND_EXTENSION_ROOT, raising=False)

    pending_root = default_pending_osmand_extension_root(package_root)

    assert pending_root == (
        external_data_home / "iPhoto" / "maps" / "tiles" / "extension.pending"
    ).resolve()
    assert default_osmand_tiles_root(package_root) == pending_root.parent
    assert default_osmand_extension_root(package_root) == bundled_root.resolve()


def test_apply_pending_osmand_extension_install_promotes_staged_directory(tmp_path) -> None:
    package_root = tmp_path / "maps"
    extension_root = _create_extension_assets(package_root)
    (extension_root / "marker.txt").write_text("old", encoding="utf-8")

    pending_root = default_pending_osmand_extension_root(package_root)
    pending_root.mkdir(parents=True, exist_ok=True)
    (pending_root / "World_basemap_2.obf").write_bytes(b"new-obf")
    (pending_root / "rendering_styles").mkdir()
    (pending_root / "rendering_styles" / "snowmobile.render.xml").write_text(
        "<renderingStyle />",
        encoding="utf-8",
    )
    (pending_root / "search").mkdir()
    (pending_root / "search" / "geonames.sqlite3").write_bytes(b"sqlite")
    (pending_root / "bin").mkdir()
    helper_name = DEFAULT_HELPER_RELATIVE_PATHS[0].name
    (pending_root / "bin" / helper_name).write_bytes(b"helper")
    (pending_root / "marker.txt").write_text("new", encoding="utf-8")

    assert apply_pending_osmand_extension_install(package_root) is True
    assert pending_root.exists() is False
    assert (default_osmand_extension_root(package_root) / "marker.txt").read_text(encoding="utf-8") == "new"


def test_apply_pending_osmand_extension_install_promotes_to_external_runtime_for_appimage(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "AppDir" / "opt" / "iPhotron" / "maps"
    bundled_root = _create_extension_assets(package_root)
    (bundled_root / "marker.txt").write_text("bundled", encoding="utf-8")
    external_data_home = tmp_path / "xdg-data"
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "iPhotron.AppImage"))
    monkeypatch.setenv("XDG_DATA_HOME", str(external_data_home))
    monkeypatch.delenv(ENV_OSMAND_EXTENSION_ROOT, raising=False)

    pending_root = default_pending_osmand_extension_root(package_root)
    pending_root.mkdir(parents=True, exist_ok=True)
    (pending_root / "World_basemap_2.obf").write_bytes(b"new-obf")
    (pending_root / "rendering_styles").mkdir()
    (pending_root / "rendering_styles" / "snowmobile.render.xml").write_text(
        "<renderingStyle />",
        encoding="utf-8",
    )
    (pending_root / "search").mkdir()
    (pending_root / "search" / "geonames.sqlite3").write_bytes(b"sqlite")
    (pending_root / "bin").mkdir()
    (pending_root / "bin" / DEFAULT_HELPER_RELATIVE_PATHS[0].name).write_bytes(b"helper")
    (pending_root / "marker.txt").write_text("external", encoding="utf-8")

    assert apply_pending_osmand_extension_install(package_root) is True
    assert pending_root.exists() is False
    assert default_osmand_extension_root(package_root) == (
        external_data_home / "iPhoto" / "maps" / "tiles" / "extension"
    ).resolve()
    assert (default_osmand_extension_root(package_root) / "marker.txt").read_text(encoding="utf-8") == "external"
    assert (bundled_root / "marker.txt").read_text(encoding="utf-8") == "bundled"
