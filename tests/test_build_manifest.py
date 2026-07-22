from __future__ import annotations

import hashlib
from pathlib import Path

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
    )

    assert manifest["schema_version"] == 1
    assert manifest["artifact_sha256"] == hashlib.sha256(
        b"candidate executable"
    ).hexdigest()
    assert len(manifest["environment_fingerprint"]) == 64
    assert manifest["environment"]["build_flags"] == ["lto=yes", "profile=test"]


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
