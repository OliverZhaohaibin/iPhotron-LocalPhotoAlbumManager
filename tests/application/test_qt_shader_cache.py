from __future__ import annotations

import json
from pathlib import Path

from iPhoto.bootstrap.qt_shader_cache import configure_shader_cache_environment


def test_configure_shader_cache_environment_prefers_library_work_dir(tmp_path) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"basic_library_path": str(library_root)}),
        encoding="utf-8",
    )
    env: dict[str, str] = {}

    cache_root = configure_shader_cache_environment(
        settings_path,
        home_root=tmp_path / "home",
        environ=env,
    )

    expected_root = library_root / ".iPhoto" / "cache" / "shaders"
    assert cache_root == expected_root
    assert expected_root.is_dir()
    assert (expected_root / "driver").is_dir()
    assert (expected_root / "qt3d").is_dir()
    assert env["__GL_SHADER_DISK_CACHE"] == "1"
    assert env["__GL_SHADER_DISK_CACHE_PATH"] == str(expected_root / "driver")
    assert env["QT3D_WRITABLE_CACHE_PATH"] == str(expected_root / "qt3d")
    assert env["QSG_RHI_PIPELINE_CACHE_SAVE"] == str(expected_root / "qt_rhi_pipeline.bin")
    assert env["QSG_RHI_PIPELINE_CACHE_LOAD"] == str(expected_root / "qt_rhi_pipeline.bin")


def test_configure_shader_cache_environment_uses_existing_legacy_work_dir(tmp_path) -> None:
    library_root = tmp_path / "library"
    legacy_work_dir = library_root / ".iphoto"
    legacy_work_dir.mkdir(parents=True)
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"basic_library_path": str(library_root)}),
        encoding="utf-8",
    )
    env: dict[str, str] = {}

    cache_root = configure_shader_cache_environment(
        settings_path,
        home_root=tmp_path / "home",
        environ=env,
    )

    expected_root = legacy_work_dir / "cache" / "shaders"
    assert cache_root == expected_root
    assert expected_root.is_dir()
    assert not any(entry.name == ".iPhoto" for entry in library_root.iterdir())


def test_configure_shader_cache_environment_falls_back_to_home_work_dir(tmp_path) -> None:
    home_root = tmp_path / "home"
    env: dict[str, str] = {}

    cache_root = configure_shader_cache_environment(
        tmp_path / "missing-settings.json",
        home_root=home_root,
        environ=env,
    )

    expected_root = home_root / ".iPhoto" / "cache" / "shaders"
    assert cache_root == expected_root
    assert expected_root.is_dir()
    assert env["__GL_SHADER_DISK_CACHE_PATH"] == str(expected_root / "driver")


def test_configure_shader_cache_environment_preserves_explicit_overrides(tmp_path) -> None:
    env = {
        "__GL_SHADER_DISK_CACHE_PATH": str(tmp_path / "custom-driver"),
        "QT3D_WRITABLE_CACHE_PATH": str(tmp_path / "custom-qt3d"),
    }

    cache_root = configure_shader_cache_environment(
        tmp_path / "missing-settings.json",
        home_root=tmp_path / "home",
        environ=env,
    )

    assert cache_root == tmp_path / "home" / ".iPhoto" / "cache" / "shaders"
    assert env["__GL_SHADER_DISK_CACHE_PATH"] == str(tmp_path / "custom-driver")
    assert env["QT3D_WRITABLE_CACHE_PATH"] == str(tmp_path / "custom-qt3d")
