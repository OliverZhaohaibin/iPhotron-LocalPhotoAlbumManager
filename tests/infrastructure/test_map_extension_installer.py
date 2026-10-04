from __future__ import annotations

from dataclasses import replace
import hashlib
import io
from pathlib import Path
import socket
import sqlite3
import sys
from contextlib import closing
import struct
import tarfile
from urllib.error import HTTPError, URLError
import zipfile

import pytest

from iPhoto.application.ports.map_extension import MapExtensionError, MapExtensionRequest
from iPhoto.application.services.map_extension_service import MapExtensionService
from iPhoto.infrastructure.services import map_extension_installer as module
from iPhoto.infrastructure.services.map_extension_installer import (
    MapExtensionInstaller,
    _install_lock,
)
from iPhoto.infrastructure.services.map_extension_packages import MapExtensionPackage
from maps import map_sources


def progress(*args):
    pass


def extension(root: Path, platform=None, marker=b"new"):
    platform = platform or sys.platform
    (root / "rendering_styles").mkdir(parents=True)
    (root / "rendering_styles" / "snowmobile.render.xml").write_text("<renderingStyle />")
    (root / "World_basemap_2.obf").write_bytes(marker)
    (root / "search").mkdir()
    with closing(sqlite3.connect(root / "search" / "geonames.sqlite3")) as db, db:
        db.execute("""CREATE TABLE search_index (
            norm_name TEXT, name_priority INTEGER, population INTEGER,
            geoname_id INTEGER, matched_name TEXT, primary_name TEXT, asciiname TEXT,
            latitude REAL, longitude REAL, feature_code TEXT, country_code TEXT,
            admin1_code TEXT, admin2_code TEXT, admin3_code TEXT, admin4_code TEXT)""")
    (root / "bin").mkdir()
    if platform == "win32":
        header = bytearray(70)
        header[:2] = b"MZ"
        struct.pack_into("<I", header, 60, 64)
        header[64:68] = b"PE\0\0"
        struct.pack_into("<H", header, 68, 0x8664 if struct.calcsize("P") == 8 else 0x14C)
        (root / "bin" / "osmand_render_helper.exe").write_bytes(header)
        for name in (
            "osmand_native_widget.dll",
            "OsmAndCore_shared.dll",
            "OsmAndCoreTools_shared.dll",
            "Qt6Core.dll",
        ):
            (root / "bin" / name).write_bytes(b"fixture")
    else:
        (root / "bin" / "osmand_render_helper").write_bytes(b"helper")


@pytest.fixture
def setup(tmp_path, monkeypatch, map_platform):
    map_platform(sys.platform)
    target = tmp_path / "用户" / "LocalAppData" / "tiles" / "extension"
    monkeypatch.setenv(map_sources.ENV_OSMAND_EXTENSION_ROOT, str(target))
    package_root = tmp_path / "Program Files" / "iPhoto" / "maps"
    package_root.mkdir(parents=True)
    source = tmp_path / "source" / "extension"
    extension(source)
    archive = tmp_path / "official.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for path in source.rglob("*"):
            if path.is_file():
                zipped.write(path, path.relative_to(source.parent))
    package = MapExtensionPackage(
        sys.platform,
        "extension.zip",
        archive.stat().st_size,
        hashlib.sha256(archive.read_bytes()).hexdigest(),
    )
    adapter = MapExtensionInstaller((package,))
    payload = MapExtensionRequest(package_root, sys.platform, archive)
    return adapter, payload, target, package


def test_offline_install_never_uses_network_and_returns_write_target(setup, monkeypatch):
    adapter, payload, target, _ = setup
    extension(payload.package_root / "tiles" / "extension", marker=b"bundled")
    monkeypatch.setattr(module.request, "build_opener", lambda *a: pytest.fail("network used"))
    result = MapExtensionService(adapter).execute(payload, progress)
    assert result.status == "installed"
    assert result.extension_root == target
    assert (target / "World_basemap_2.obf").read_bytes() == b"new"
    assert (payload.package_root / "tiles/extension/World_basemap_2.obf").read_bytes() == b"bundled"
    assert not result.pending_root.exists()


def test_write_probe_happens_before_download(setup, monkeypatch):
    adapter, payload, target, _ = setup
    original = Path.write_bytes

    def deny(path, data):
        if path.name == "write":
            raise PermissionError(13, "denied")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", deny)
    monkeypatch.setattr(adapter, "_download", lambda *a: pytest.fail("download before probe"))
    with pytest.raises(MapExtensionError) as caught:
        adapter.execute(replace(payload, local_archive_path=None), progress)
    assert caught.value.category == "permission"
    assert caught.value.stage == "prepare"
    assert str(target) in caught.value.detail


def test_pending_is_not_reported_as_installed_and_preparation_activates(setup):
    adapter, payload, target, _ = setup
    extension(target, marker=b"old")
    result = adapter.execute(replace(payload, defer_activation=True), progress)
    assert result.status == "pending_restart"
    assert (target / "World_basemap_2.obf").read_bytes() == b"old"
    result = adapter.execute(replace(payload, operation="prepare"), progress)
    assert result.status == "installed"
    assert (target / "World_basemap_2.obf").read_bytes() == b"new"


def test_legacy_pending_is_copied_and_left_untouched(setup):
    adapter, payload, target, _ = setup
    legacy = payload.package_root / "tiles/extension.pending"
    extension(legacy)
    result = adapter.execute(replace(payload, operation="prepare"), progress)
    assert result.status == "installed"
    assert legacy.is_dir()
    assert (target / "World_basemap_2.obf").exists()


def test_corrupt_pending_does_not_replace_old_install(setup):
    adapter, payload, target, _ = setup
    extension(target, marker=b"old")
    target.with_name("extension.pending").mkdir()
    with pytest.raises(MapExtensionError, match="incomplete"):
        adapter.execute(replace(payload, operation="prepare"), progress)
    assert (target / "World_basemap_2.obf").read_bytes() == b"old"


def test_recovers_backup_after_process_exit(setup):
    adapter, payload, target, _ = setup
    extension(target.with_name("extension.backup"), marker=b"old")
    # Explicit platform keeps this fixture independent of the host OS.
    adapter.execute(replace(payload, operation="prepare"), progress)
    assert (target / "World_basemap_2.obf").read_bytes() == b"old"


def test_activation_failure_restores_previous_install(setup, monkeypatch):
    adapter, payload, target, _ = setup
    extension(target, marker=b"old")
    original = Path.replace

    def fail(path, destination):
        if path.name == "extension.pending":
            raise OSError(5, "failure")
        return original(path, destination)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(MapExtensionError):
        adapter.execute(payload, progress)
    assert (target / "World_basemap_2.obf").read_bytes() == b"old"
    assert target.with_name("extension.pending").is_dir()


def test_windows_locked_install_remains_pending(setup, monkeypatch):
    adapter, payload, target, _ = setup
    extension(target, "win32", b"old")
    pending = target.with_name("extension.pending")
    extension(pending, "win32")
    monkeypatch.setattr(
        map_sources,
        "apply_pending_osmand_extension_install",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "locked")),
    )
    result = adapter.execute(replace(payload, platform="win32", operation="prepare"), progress)
    assert result.status == "pending_restart"
    assert pending.exists()
    assert (target / "World_basemap_2.obf").read_bytes() == b"old"


def test_cleanup_error_does_not_turn_success_into_failure(setup, monkeypatch):
    adapter, payload, target, _ = setup
    extension(target, marker=b"old")
    original = module.shutil.rmtree

    def fail(path, *a, **k):
        if Path(path).name == "extension.backup":
            raise PermissionError("locked")
        return original(path, *a, **k)

    monkeypatch.setattr(module.shutil, "rmtree", fail)
    assert adapter.execute(payload, progress).status == "installed"
    assert (target / "World_basemap_2.obf").read_bytes() == b"new"


@pytest.mark.parametrize(
    "change,category",
    [
        ("hash", "integrity"),
        ("size", "unknown_package"),
        ("platform", "unsupported"),
        ("version", "unsupported"),
    ],
)
def test_package_identity_checks(setup, change, category):
    adapter, payload, target, package = setup
    if change == "hash":
        adapter = MapExtensionInstaller((replace(package, sha256="0" * 64),))
    elif change == "size":
        payload.local_archive_path.write_bytes(b"truncated")
    elif change == "platform":
        payload = replace(payload, platform="linux" if payload.platform == "win32" else "win32")
    else:
        adapter = MapExtensionInstaller((replace(package, app_major=99),))
    with pytest.raises(MapExtensionError) as caught:
        adapter.execute(payload, progress)
    assert caught.value.category == category
    assert not target.exists()


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/outside",
        "C:/outside",
        "extension/../outside",
        "extension\\..\\outside",
        "extension/file:stream",
        "extension/name. ",
    ],
)
def test_unsafe_zip_member_rejected(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(name, b"bad")
    with pytest.raises(MapExtensionError, match="unsafe_archive"):
        MapExtensionInstaller()._extract(archive, tmp_path / "extract")
    assert not (tmp_path / "outside").exists()


def test_tar_escaping_link_rejected(tmp_path):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as tar:
        member = tarfile.TarInfo("extension/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../outside"
        tar.addfile(member)
    with pytest.raises(tarfile.FilterError):
        MapExtensionInstaller()._extract(archive, tmp_path / "extract")


def test_install_lock_excludes_other_installers(tmp_path):
    with _install_lock(tmp_path / "lock"):
        with pytest.raises(MapExtensionError, match="busy"):
            with _install_lock(tmp_path / "lock"):
                pytest.fail("second writer acquired lock")


class Response(io.BytesIO):
    def __init__(self, data, size):
        super().__init__(data)
        self.headers = {"Content-Length": str(size)}


def test_fresh_proxy_on_retry_and_cache_reused_after_install_failure(setup, monkeypatch):
    adapter, payload, target, package = setup
    data = payload.local_archive_path.read_bytes()
    proxies = iter([{"https": "http://first:123"}, {"https": "http://second:456"}])
    monkeypatch.setattr(module.request, "getproxies", lambda: next(proxies))
    handlers = []

    def build(handler):
        handlers.append(handler.proxies)

        class Opener:
            def open(self, req, timeout):
                assert timeout == 30
                if len(handlers) == 1:
                    raise URLError(socket.timeout())
                return Response(data, len(data))

        return Opener()

    monkeypatch.setattr(module.request, "build_opener", build)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    original = adapter._stage_archive
    monkeypatch.setattr(
        adapter, "_stage_archive", lambda *a: (_ for _ in ()).throw(PermissionError("locked"))
    )
    with pytest.raises(MapExtensionError):
        adapter.execute(replace(payload, local_archive_path=None), progress)
    assert handlers == [{"https": "http://first:123"}, {"https": "http://second:456"}]
    monkeypatch.setattr(adapter, "_stage_archive", original)
    monkeypatch.setattr(
        module.request, "build_opener", lambda *a: pytest.fail("cached archive downloaded again")
    )
    assert (
        adapter.execute(replace(payload, local_archive_path=None), progress).status == "installed"
    )


def test_direct_uses_empty_proxy_without_mutating_global_opener(setup, monkeypatch):
    adapter, payload, target, package = setup
    data = payload.local_archive_path.read_bytes()
    global_opener = module.request._opener

    def build(handler):
        assert handler.proxies == {}

        class Opener:
            def open(self, *a, **k):
                return Response(data, len(data))

        return Opener()

    monkeypatch.setattr(module.request, "build_opener", build)
    monkeypatch.setattr(
        module.request, "getproxies", lambda: pytest.fail("proxy read in direct mode")
    )
    adapter.execute(replace(payload, local_archive_path=None, network_mode="direct"), progress)
    assert module.request._opener is global_opener


@pytest.mark.parametrize(
    "reason,category",
    [
        (ConnectionRefusedError(10061, "refused"), "refused"),
        (socket.gaierror(-2, "name"), "dns"),
        (module.ssl.SSLCertVerificationError("certificate"), "tls"),
        (HTTPError("https://user:secret@host/path?token=secret", 403, "error", {}, None), "http"),
    ],
)
def test_network_errors_do_not_leak_credentials_or_retry_nontransient(
    setup, monkeypatch, reason, category
):
    adapter, payload, target, _ = setup
    monkeypatch.setattr(
        module.request,
        "getproxies",
        lambda: {"https": "http://user:password@127.0.0.1:7890/?token=secret"},
    )
    monkeypatch.setattr(module.request, "proxy_bypass", lambda _: False)

    class Opener:
        def open(self, *a, **k):
            raise reason if isinstance(reason, HTTPError) else URLError(reason)

    monkeypatch.setattr(module.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(module.time, "sleep", lambda _: pytest.fail("unexpected retry"))
    with pytest.raises(MapExtensionError) as caught:
        adapter.execute(replace(payload, local_archive_path=None), progress)
    assert caught.value.category == category
    assert "127.0.0.1:7890" in str(caught.value)
    assert "password" not in str(caught.value)
    assert "secret" not in str(caught.value)
    assert not list(target.parent.rglob("*.part"))


def test_truncated_response_retries_at_most_three_times(setup, monkeypatch):
    adapter, payload, _, package = setup
    attempts = []

    class Opener:
        def open(self, *a, **k):
            attempts.append(1)
            return Response(b"short", package.size)

    monkeypatch.setattr(module.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    with pytest.raises(MapExtensionError, match="interrupted"):
        adapter.execute(replace(payload, local_archive_path=None), progress)
    assert len(attempts) == 3


def test_macos_bundled_tar_is_verified_and_installed_offline(
    setup, tmp_path, monkeypatch, map_platform
):
    map_platform("darwin")
    adapter, payload, target, _ = setup
    source = tmp_path / "mac" / "extension"
    extension(source, "darwin")
    archive = tmp_path / "extension.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(source, arcname="extension")
    archive.with_suffix(".tar.sha256").write_text(hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(map_sources, "bundled_osmand_extension_archive", lambda _: archive)
    monkeypatch.setattr(module.request, "build_opener", lambda *a: pytest.fail("network used"))
    assert (
        adapter.execute(replace(payload, operation="prepare", platform="darwin"), progress).status
        == "installed"
    )


def test_runtime_uses_only_complete_selected_root(setup):
    _, payload, target, _ = setup
    extension(payload.package_root / "tiles/extension")
    (target / "bin").mkdir(parents=True)
    helper_name = (
        "osmand_render_helper.exe" if payload.platform == "win32" else "osmand_render_helper"
    )
    (target / "bin" / helper_name).write_bytes(b"incomplete")
    assert map_sources.resolve_osmand_helper_command(payload.package_root) == (
        str(payload.package_root / "tiles/extension/bin" / helper_name),
    )


def test_tar_extension_root_must_not_be_symlink(tmp_path):
    root = tmp_path / "extension"
    try:
        root.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is not available for this account")
    with pytest.raises(MapExtensionError, match="unsafe_archive"):
        MapExtensionInstaller()._validate(root, "linux")


def test_actual_windows_layout_uses_user_profile_for_offline_install(
    tmp_path, monkeypatch, map_platform
):
    map_platform("win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "用户"))
    monkeypatch.delenv(map_sources.ENV_OSMAND_EXTENSION_ROOT, raising=False)
    package_root = tmp_path / "Program Files" / "maps"
    package_root.mkdir(parents=True)
    source = tmp_path / "source" / "extension"
    extension(source, "win32")
    archive = tmp_path / "extension.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for path in source.rglob("*"):
            if path.is_file():
                zipped.write(path, path.relative_to(source.parent))
    descriptor = MapExtensionPackage(
        "win32",
        "extension.zip",
        archive.stat().st_size,
        hashlib.sha256(archive.read_bytes()).hexdigest(),
    )
    adapter = MapExtensionInstaller((descriptor,))
    result = adapter.execute(MapExtensionRequest(package_root, "win32", archive), progress)
    assert result.status == "installed"
    assert result.extension_root == tmp_path / "用户/iPhoto/extensions/maps/v1/tiles/extension"
    assert list(package_root.iterdir()) == []


def test_wrong_native_architecture_does_not_activate(tmp_path):
    root = tmp_path / "extension"
    extension(root, "win32")
    helper = root / "bin/osmand_render_helper.exe"
    header = bytearray(helper.read_bytes())
    struct.pack_into("<H", header, 68, 0xAA64)
    helper.write_bytes(header)
    with pytest.raises(MapExtensionError, match="unsupported"):
        MapExtensionInstaller()._validate(root, "win32")


def test_mismatched_content_length_is_not_extracted(setup, monkeypatch):
    adapter, payload, target, package = setup

    class Opener:
        def open(self, *args, **kwargs):
            return Response(b"wrong", 5)

    monkeypatch.setattr(module.request, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(module.time, "sleep", lambda _: pytest.fail("integrity error retried"))
    with pytest.raises(MapExtensionError, match="integrity"):
        adapter.execute(replace(payload, local_archive_path=None), progress)
    assert not target.exists()


def test_disk_full_is_reported_before_network(setup, monkeypatch):
    adapter, payload, _, _ = setup
    original = Path.write_bytes

    def full(path, data):
        if path.name == "write":
            raise OSError(module.errno.ENOSPC, "disk full")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", full)
    monkeypatch.setattr(adapter, "_download", lambda *args: pytest.fail("network used"))
    with pytest.raises(MapExtensionError) as caught:
        adapter.execute(replace(payload, local_archive_path=None), progress)
    assert caught.value.category == "disk"


@pytest.mark.parametrize("fail", [False, True])
def test_extension_fixture_releases_sqlite_before_rename(tmp_path, monkeypatch, fail):
    connections = []
    connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, query, *args):
            if fail:
                raise sqlite3.OperationalError("fixture failure")
            return super().execute(query, *args)

    def tracked(*args, **kwargs):
        conn = connect(*args, factory=Connection, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked)
    root = tmp_path / "extension"
    try:
        if fail:
            with pytest.raises(sqlite3.OperationalError, match="fixture failure"):
                extension(root)
        else:
            extension(root)
        assert len(connections) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            sqlite3.Connection.execute(connections[0], "SELECT 1")
        renamed = tmp_path / "renamed"
        root.rename(renamed)
        (renamed / "search/geonames.sqlite3").unlink()
    finally:
        for connection in connections:
            connection.close()


def test_default_install_fixture_matches_host_platform(setup):
    adapter, payload, target, package = setup
    assert payload.platform == sys.platform == package.platform == map_sources.sys.platform
    result = adapter.execute(payload, progress)
    helper = (
        target
        / "bin"
        / ("osmand_render_helper.exe" if sys.platform == "win32" else "osmand_render_helper")
    )
    assert helper.is_file()
    assert map_sources.resolve_osmand_helper_command(payload.package_root) == (str(helper),)
    assert result.status == "installed"
