import sqlite3
from pathlib import Path

from maps import map_sources
from maps.map_sources import (
    DEFAULT_HELPER_RELATIVE_PATHS,
    DEFAULT_NATIVE_WIDGET_RELATIVE_PATHS,
    ENV_OSMAND_EXTENSION_ROOT,
    MapSourceSpec,
    apply_pending_osmand_extension_install,
    _sdk_roots,
    default_osmand_extension_root,
    default_osmand_download_url,
    default_osmand_search_database,
    default_osmand_tiles_root,
    default_pending_osmand_extension_root,
    has_usable_osmand_default,
    has_installed_osmand_extension,
    has_usable_osmand_search_extension,
    is_valid_osmand_search_database,
    resolve_osmand_helper_command,
    resolve_osmand_native_widget_library,
)


def _create_extension_assets(package_root: Path) -> Path:
    extension_root = default_osmand_extension_root(package_root)
    _create_extension_assets_at(extension_root)
    return extension_root


def _create_extension_assets_at(extension_root: Path) -> Path:
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
    _create_search_database(extension_root / "search" / "geonames.sqlite3")
    helper_path = extension_root / DEFAULT_HELPER_RELATIVE_PATHS[0].relative_to(
        Path("tiles") / "extension"
    )
    helper_path.write_bytes(b"helper")
    return extension_root


def _create_search_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE search_index (
                norm_name TEXT NOT NULL,
                name_priority INTEGER NOT NULL,
                population INTEGER NOT NULL,
                geoname_id INTEGER NOT NULL,
                matched_name TEXT NOT NULL,
                primary_name TEXT NOT NULL,
                asciiname TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                feature_code TEXT,
                country_code TEXT,
                admin1_code TEXT,
                admin2_code TEXT,
                admin3_code TEXT,
                admin4_code TEXT,
                PRIMARY KEY (norm_name, name_priority, population DESC, geoname_id)
            ) WITHOUT ROWID;
            CREATE TABLE prefix_cache (
                prefix TEXT NOT NULL,
                rank INTEGER NOT NULL,
                name_priority INTEGER NOT NULL,
                population INTEGER NOT NULL,
                geoname_id INTEGER NOT NULL,
                matched_name TEXT NOT NULL,
                primary_name TEXT NOT NULL,
                asciiname TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                feature_code TEXT,
                country_code TEXT,
                admin1_code TEXT,
                admin2_code TEXT,
                admin3_code TEXT,
                admin4_code TEXT,
                PRIMARY KEY (prefix, rank)
            ) WITHOUT ROWID;
            """
        )


def _create_search_database_without_prefix_cache(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE search_index (
                norm_name TEXT NOT NULL,
                name_priority INTEGER NOT NULL,
                population INTEGER NOT NULL,
                geoname_id INTEGER NOT NULL,
                matched_name TEXT NOT NULL,
                primary_name TEXT NOT NULL,
                asciiname TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                feature_code TEXT,
                country_code TEXT,
                admin1_code TEXT,
                admin2_code TEXT,
                admin3_code TEXT,
                admin4_code TEXT,
                PRIMARY KEY (norm_name, name_priority, population DESC, geoname_id)
            ) WITHOUT ROWID;
            """
        )


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
    monkeypatch.setattr(map_sources.sys, "platform", "linux")
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
    if map_sources.os.name == "nt":
        monkeypatch.setenv("APPDATA", str(tmp_path / "empty-appdata"))
    else:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty-data-home"))
    monkeypatch.delenv("APPIMAGE", raising=False)
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


def test_darwin_runtime_candidates_prefer_extension_before_sdk(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "repo" / "src" / "maps"
    package_root.mkdir(parents=True)
    (tmp_path / "PySide6-OsmAnd-SDK").mkdir()
    extension_root = package_root / "tiles" / "extension"
    helper_rel = Path("tiles") / "extension" / "bin" / "osmand_render_helper"
    widget_rel = Path("tiles") / "extension" / "bin" / "osmand_native_widget.dylib"
    sdk_helper_rel = (
        Path("tools") / "osmand_render_helper_native" / "dist-macosx" / "osmand_render_helper"
    )
    sdk_widget_rel = (
        Path("tools")
        / "osmand_render_helper_native"
        / "dist-macosx"
        / "osmand_native_widget.dylib"
    )

    monkeypatch.setattr(map_sources.sys, "platform", "darwin")
    monkeypatch.setattr(map_sources, "DEFAULT_HELPER_RELATIVE_PATHS", (helper_rel,))
    monkeypatch.setattr(map_sources, "SDK_HELPER_RELATIVE_PATHS", (sdk_helper_rel,))
    monkeypatch.setattr(map_sources, "DEFAULT_NATIVE_WIDGET_RELATIVE_PATHS", (widget_rel,))
    monkeypatch.setattr(map_sources, "SDK_NATIVE_WIDGET_RELATIVE_PATHS", (sdk_widget_rel,))
    monkeypatch.setenv(ENV_OSMAND_EXTENSION_ROOT, str(extension_root))

    helper_candidates = map_sources._default_helper_candidates(package_root)
    widget_candidates = map_sources._default_native_widget_candidates(package_root)

    assert helper_candidates[:2] == (
        (package_root / helper_rel).resolve(),
        (tmp_path / "PySide6-OsmAnd-SDK" / sdk_helper_rel).resolve(),
    )
    assert widget_candidates[:2] == (
        (package_root / widget_rel).resolve(),
        (tmp_path / "PySide6-OsmAnd-SDK" / sdk_widget_rel).resolve(),
    )


def test_linux_runtime_candidates_keep_sdk_before_extension(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "repo" / "src" / "maps"
    package_root.mkdir(parents=True)
    (tmp_path / "PySide6-OsmAnd-SDK").mkdir()
    extension_root = package_root / "tiles" / "extension"
    helper_rel = Path("tiles") / "extension" / "bin" / "osmand_render_helper"
    widget_rel = Path("tiles") / "extension" / "bin" / "osmand_native_widget.so"
    sdk_helper_rel = (
        Path("tools") / "osmand_render_helper_native" / "dist-linux" / "osmand_render_helper"
    )
    sdk_widget_rel = (
        Path("tools") / "osmand_render_helper_native" / "dist-linux" / "osmand_native_widget.so"
    )

    monkeypatch.setattr(map_sources.sys, "platform", "linux")
    monkeypatch.setattr(map_sources, "DEFAULT_HELPER_RELATIVE_PATHS", (helper_rel,))
    monkeypatch.setattr(map_sources, "SDK_HELPER_RELATIVE_PATHS", (sdk_helper_rel,))
    monkeypatch.setattr(map_sources, "DEFAULT_NATIVE_WIDGET_RELATIVE_PATHS", (widget_rel,))
    monkeypatch.setattr(map_sources, "SDK_NATIVE_WIDGET_RELATIVE_PATHS", (sdk_widget_rel,))
    monkeypatch.setenv(ENV_OSMAND_EXTENSION_ROOT, str(extension_root))

    helper_candidates = map_sources._default_helper_candidates(package_root)
    widget_candidates = map_sources._default_native_widget_candidates(package_root)

    assert helper_candidates[:2] == (
        (tmp_path / "PySide6-OsmAnd-SDK" / sdk_helper_rel).resolve(),
        (package_root / helper_rel).resolve(),
    )
    assert widget_candidates[:2] == (
        (tmp_path / "PySide6-OsmAnd-SDK" / sdk_widget_rel).resolve(),
        (package_root / widget_rel).resolve(),
    )


def test_win32_runtime_candidates_ignore_sdk_and_keep_windows_filenames(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "repo" / "src" / "maps"
    package_root.mkdir(parents=True)
    (tmp_path / "PySide6-OsmAnd-SDK").mkdir()
    extension_root = package_root / "tiles" / "extension"
    helper_rels = (
        Path("tiles") / "extension" / "bin" / "osmand_render_helper.exe",
        Path("tiles") / "extension" / "bin" / "osmand_render_helper_sdk.exe",
    )
    widget_rels = (
        Path("tiles") / "extension" / "bin" / "osmand_native_widget.dll",
        Path("tiles") / "extension" / "bin" / "libosmand_native_widget.dll",
    )

    monkeypatch.setattr(map_sources.sys, "platform", "win32")
    monkeypatch.setattr(map_sources, "DEFAULT_HELPER_RELATIVE_PATHS", helper_rels)
    monkeypatch.setattr(map_sources, "SDK_HELPER_RELATIVE_PATHS", ())
    monkeypatch.setattr(map_sources, "DEFAULT_NATIVE_WIDGET_RELATIVE_PATHS", widget_rels)
    monkeypatch.setattr(
        map_sources,
        "SDK_NATIVE_WIDGET_RELATIVE_PATHS",
        (
            Path("tools")
            / "osmand_render_helper_native"
            / "dist-msvc"
            / "osmand_native_widget.dll",
        ),
    )
    monkeypatch.setenv(ENV_OSMAND_EXTENSION_ROOT, str(extension_root))

    helper_candidates = map_sources._default_helper_candidates(package_root)
    widget_candidates = map_sources._default_native_widget_candidates(package_root)

    assert helper_candidates == tuple((package_root / rel).resolve() for rel in helper_rels)
    assert widget_candidates == tuple((package_root / rel).resolve() for rel in widget_rels)


def test_has_installed_osmand_extension_requires_search_database_and_helper(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "maps"
    _create_extension_assets(package_root)
    if map_sources.os.name == "nt":
        monkeypatch.setenv("APPDATA", str(tmp_path / "empty-appdata"))
    else:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty-data-home"))
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv(ENV_OSMAND_EXTENSION_ROOT, raising=False)

    assert has_installed_osmand_extension(package_root) is True

    search_db = default_osmand_extension_root(package_root) / "search" / "geonames.sqlite3"
    search_db.unlink()
    assert has_installed_osmand_extension(package_root) is False


def test_osmand_search_extension_rejects_lfs_pointer_database(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "maps"
    extension_root = _create_extension_assets(package_root)
    search_db = extension_root / "search" / "geonames.sqlite3"
    search_db.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:86f6d278e320740d81a02bf6d59eee452e79421e0b7b959a9cbfa066ea22db97\n"
        "size 345563136\n",
        encoding="utf-8",
    )
    if map_sources.os.name == "nt":
        monkeypatch.setenv("APPDATA", str(tmp_path / "empty-appdata"))
    else:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty-data-home"))
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv(ENV_OSMAND_EXTENSION_ROOT, raising=False)

    assert is_valid_osmand_search_database(search_db) is False
    assert has_usable_osmand_search_extension(package_root) is False
    assert has_installed_osmand_extension(package_root) is False


def test_osmand_search_extension_accepts_optimized_database_without_prefix_cache(
    tmp_path,
) -> None:
    search_db = tmp_path / "geonames.sqlite3"
    _create_search_database_without_prefix_cache(search_db)

    assert is_valid_osmand_search_database(search_db) is True


def test_has_installed_osmand_extension_detects_external_runtime_when_bundled_exists(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "maps"
    bundled_root = package_root / "tiles" / "extension"
    (bundled_root / "rendering_styles").mkdir(parents=True, exist_ok=True)
    (bundled_root / "rendering_styles" / "snowmobile.render.xml").write_text(
        "<renderingStyle />",
        encoding="utf-8",
    )
    external_data_home = tmp_path / "xdg-data"
    if map_sources.os.name == "nt":
        monkeypatch.setenv("APPDATA", str(external_data_home))
        external_root = external_data_home / "iPhoto" / "maps" / "tiles" / "extension"
    else:
        monkeypatch.setattr(map_sources.sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(external_data_home))
        external_root = external_data_home / "iPhoto" / "maps" / "tiles" / "extension"
    _create_extension_assets_at(external_root)
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv(ENV_OSMAND_EXTENSION_ROOT, raising=False)

    assert default_osmand_extension_root(package_root) == bundled_root.resolve()
    assert has_installed_osmand_extension(package_root) is True


def test_default_osmand_extension_root_uses_external_runtime_path_for_appimage(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "AppDir" / "opt" / "iPhotron" / "maps"
    package_root.mkdir(parents=True)
    external_data_home = tmp_path / "xdg-data"
    monkeypatch.setattr(map_sources.sys, "platform", "linux")
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


def test_default_osmand_extension_root_falls_back_to_valid_bundled_extension_when_managed_copy_is_incomplete(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "maps"
    bundled_root = _create_extension_assets(package_root)
    override_root = tmp_path / "runtime" / "extension"
    override_root.mkdir(parents=True)
    (override_root / "marker.txt").write_text("partial", encoding="utf-8")
    monkeypatch.setenv(ENV_OSMAND_EXTENSION_ROOT, str(override_root))

    source = MapSourceSpec.default(package_root)

    assert default_osmand_extension_root(package_root) == bundled_root.resolve()
    assert has_usable_osmand_default(package_root) is True
    assert source.kind == "osmand_obf"
    assert Path(source.data_path) == bundled_root / "World_basemap_2.obf"


def test_default_pending_osmand_extension_root_uses_external_runtime_path_for_appimage_when_bundled_exists(
    tmp_path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "AppDir" / "opt" / "iPhotron" / "maps"
    bundled_root = package_root / "tiles" / "extension"
    bundled_root.mkdir(parents=True, exist_ok=True)
    external_data_home = tmp_path / "xdg-data"
    monkeypatch.setattr(map_sources.sys, "platform", "linux")
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "iPhotron.AppImage"))
    monkeypatch.setenv("XDG_DATA_HOME", str(external_data_home))
    monkeypatch.delenv(ENV_OSMAND_EXTENSION_ROOT, raising=False)

    pending_root = default_pending_osmand_extension_root(package_root)

    assert pending_root == (
        external_data_home / "iPhoto" / "maps" / "tiles" / "extension.pending"
    ).resolve()
    assert default_osmand_tiles_root(package_root) == pending_root.parent
    assert default_osmand_extension_root(package_root) == bundled_root.resolve()


def test_windows_map_components_use_versioned_local_app_data(monkeypatch, tmp_path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    package_root = tmp_path / "package" / "maps"
    package_root.mkdir(parents=True)
    monkeypatch.setattr("maps.map_sources.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    pending_root = default_pending_osmand_extension_root(package_root)

    assert pending_root == (
        local_app_data
        / "iPhoto"
        / "extensions"
        / "maps"
        / "v1"
        / "tiles"
        / "extension.pending"
    ).resolve()


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
    _create_search_database(pending_root / "search" / "geonames.sqlite3")
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
    monkeypatch.setattr(map_sources.sys, "platform", "linux")
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
    _create_search_database(pending_root / "search" / "geonames.sqlite3")
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


def test_sdk_roots_discovers_inner_checkout(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    inner_sdk = repo_root / "PySide6-OsmAnd-SDK"
    inner_sdk.mkdir(parents=True)

    roots = _sdk_roots(repo_root)

    assert inner_sdk in roots


def test_sdk_roots_discovers_sibling_checkout(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    sibling_sdk = tmp_path / "PySide6-OsmAnd-SDK"
    sibling_sdk.mkdir()

    roots = _sdk_roots(repo_root)

    assert sibling_sdk in roots


def test_sdk_roots_discovers_both_when_both_exist(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    inner_sdk = repo_root / "PySide6-OsmAnd-SDK"
    inner_sdk.mkdir(parents=True)
    sibling_sdk = tmp_path / "PySide6-OsmAnd-SDK"
    sibling_sdk.mkdir()

    roots = _sdk_roots(repo_root)

    assert inner_sdk in roots
    assert sibling_sdk in roots


def test_sdk_roots_returns_empty_when_neither_exists(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    roots = _sdk_roots(repo_root)

    assert roots == ()
