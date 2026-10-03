from __future__ import annotations

import hashlib
from pathlib import Path

import tools.build_manifest as build_manifest_module
from tools.build_manifest import create_manifest


def test_build_manifest_separates_environment_and_artifact_identity(tmp_path: Path) -> None:
    artifact = tmp_path / "app.bin"
    artifact.write_bytes(b"candidate executable")
    driver = tmp_path / "build.sh"
    driver.write_text("nuitka --standalone\n", encoding="utf-8")
    native = tmp_path / "native"
    native.mkdir()
    (native / "helper").write_bytes(b"native")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "shader.qsb").write_bytes(b"shader")

    manifest = create_manifest(
        root=tmp_path,
        artifact=artifact,
        build_driver=driver,
        build_flags=["lto=yes", "profile=test"],
        native_runtime=native,
        assets=[assets],
        artifact_tree=assets,
    )

    assert manifest["schema_version"] == 1
    assert manifest["artifact_sha256"] == hashlib.sha256(
        b"candidate executable"
    ).hexdigest()
    assert manifest["artifact_tree_path"] == "assets"
    assert len(manifest["artifact_tree_sha256"]) == 64
    assert len(manifest["environment_fingerprint"]) == 64
    assert manifest["environment"]["build_flags"] == ["lto=yes", "profile=test"]
    assert manifest["environment"]["build_host"]["system"]
    assert manifest["environment"]["build_host"]["machine"]
    assert "distro_id" in manifest["environment"]["build_host"]
    assert "libc_version" in manifest["environment"]["build_host"]


def test_environment_fingerprint_changes_with_effective_build_flags(tmp_path: Path) -> None:
    artifact = tmp_path / "app.bin"
    artifact.write_bytes(b"same executable")
    driver = tmp_path / "build.sh"
    driver.write_text("build\n", encoding="utf-8")

    first = create_manifest(
        root=tmp_path,
        artifact=artifact,
        build_driver=driver,
        build_flags=["low_memory=0"],
        native_runtime=None,
        assets=[],
    )
    second = create_manifest(
        root=tmp_path,
        artifact=artifact,
        build_driver=driver,
        build_flags=["low_memory=1"],
        native_runtime=None,
        assets=[],
    )

    assert first["environment_fingerprint"] != second["environment_fingerprint"]


def test_build_manifest_records_linux_distribution_and_libc(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "entrypoint.bin"
    artifact.write_bytes(b"elf")
    driver = tmp_path / "build.sh"
    driver.write_text("build\n", encoding="utf-8")
    monkeypatch.setattr(build_manifest_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(build_manifest_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(build_manifest_module.platform, "release", lambda: "6.8.0")
    monkeypatch.setattr(
        build_manifest_module.platform,
        "freedesktop_os_release",
        lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"},
    )
    monkeypatch.setattr(
        build_manifest_module.platform,
        "libc_ver",
        lambda: ("glibc", "2.39"),
    )

    manifest = create_manifest(
        root=tmp_path,
        artifact=artifact,
        build_driver=driver,
        build_flags=[],
        native_runtime=None,
        assets=[],
    )

    assert manifest["environment"]["build_host"] == {
        "system": "Linux",
        "machine": "x86_64",
        "platform_release": "6.8.0",
        "distro_id": "ubuntu",
        "distro_version_id": "24.04",
        "libc_name": "glibc",
        "libc_version": "2.39",
    }


def test_manifest_records_app_and_map_package_identity(tmp_path):
    import shutil
    repository = Path(__file__).resolve().parents[1]
    package = Path("src/iPhoto/infrastructure/services/map_extension_packages.py")
    (tmp_path / package.parent).mkdir(parents=True)
    shutil.copyfile(repository / package, tmp_path / package)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "6.6.8"\n')
    artifact = tmp_path / "app.exe"
    artifact.write_bytes(b"exe")
    manifest = create_manifest(root=tmp_path, artifact=artifact, build_driver=artifact,
                               build_flags=[], native_runtime=None, assets=[])
    assert manifest["app_version"] == "6.6.8"
    windows = next(p for p in manifest["map_extension_packages"] if p["platform"] == "win32")
    assert windows["size"] == 538920928
    assert len(windows["sha256"]) == 64
