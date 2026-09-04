from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest

from tools.build_manifest import _sha256_path
from tools.check_flatpak_elf_abi import audit_payload, parse_version_needs

LIMITS = {
    "GLIBC": "2.42",
    "GLIBCXX": "3.4.34",
    "CXXABI": "1.3.15",
}
SOURCE_REVISION = "current-revision"
PROJECT_VERSION = "6.6.8"


def elf64_header(*, machine: int = 62) -> bytes:
    identity = bytearray(16)
    identity[:4] = b"\x7fELF"
    identity[4] = 2
    identity[5] = 1
    identity[6] = 1
    return bytes(identity) + struct.pack("<HHI", 2, machine, 1)


def write_png(path: Path, *, width: int = 256, height: int = 256) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    scanlines = b"".join(b"\x00" + (b"\x00\x00\x00\xff" * width) for _ in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )


def _write_manifest(
    path: Path,
    entrypoint: Path,
    build_driver: Path,
    *,
    source_revision: str = SOURCE_REVISION,
    project_version: str = PROJECT_VERSION,
    **host_overrides: str,
) -> None:
    build_host = {
        "system": "Linux",
        "machine": "x86_64",
        "distro_id": "ubuntu",
        "distro_version_id": "24.04",
        "libc_name": "glibc",
        "libc_version": "2.39",
    }
    build_host.update(host_overrides)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_revision": source_revision,
                "artifact_path": entrypoint.name,
                "artifact_sha256": hashlib.sha256(entrypoint.read_bytes()).hexdigest(),
                "artifact_tree_path": entrypoint.parent.name,
                "artifact_tree_sha256": _sha256_path(entrypoint.parent),
                "environment": {
                    "build_host": build_host,
                    "build_driver_sha256": hashlib.sha256(
                        build_driver.read_bytes()
                    ).hexdigest(),
                    "build_flags": [f"project_version={project_version}"],
                },
            }
        ),
        encoding="utf-8",
    )


def _write_readelf(
    path: Path,
    *,
    glibc: str = "2.34",
    glibcxx: str = "3.4.29",
    cxxabi: str = "1.3.13",
) -> None:
    path.write_text(
        f"""#!/bin/sh
cat <<'EOF'
Version symbols section '.gnu.version' contains 2 entries:
  000:   0 (*local*)
Version needs section '.gnu.version_r' contains 1 entry:
  0x0010: Name: GLIBC_{glibc}  Flags: none  Version: 4
  0x0020: Name: GLIBCXX_{glibcxx}  Flags: none  Version: 3
  0x0030: Name: CXXABI_{cxxabi}  Flags: none  Version: 2
Version definition section '.gnu.version_d' contains 1 entry:
  0x0040: Name: GLIBC_9.99  Flags: none  Version: 1
EOF
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "entrypoint.dist"
    root.mkdir()
    entrypoint = root / "entrypoint.bin"
    entrypoint.write_bytes(elf64_header() + b"fixture")
    entrypoint.chmod(0o755)
    build_driver = tmp_path / "build_nuitka_fast.sh"
    build_driver.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    manifest = tmp_path / "build-manifest.json"
    _write_manifest(manifest, entrypoint, build_driver)
    icon = tmp_path / "icon.png"
    write_png(icon)
    readelf = tmp_path / "readelf"
    _write_readelf(readelf)
    return root, entrypoint, manifest, icon


def _audit(
    *,
    root: Path,
    entrypoint: Path,
    manifest: Path,
    icon: Path,
) -> dict:
    return audit_payload(
        root=root,
        entrypoint=entrypoint,
        build_manifest=manifest,
        icon=icon,
        expected_source_revision=SOURCE_REVISION,
        expected_project_version=PROJECT_VERSION,
        expected_build_driver=manifest.parent / "build_nuitka_fast.sh",
        limits=LIMITS,
        readelf_bin=str(manifest.parent / "readelf"),
    )


def test_parse_version_needs_ignores_version_definitions() -> None:
    output = """Version needs section '.gnu.version_r' contains 1 entry:
  0x0010: Name: GLIBC_2.34 Flags: none
Version definition section '.gnu.version_d' contains 1 entry:
  0x0020: Name: GLIBC_9.99 Flags: none
"""
    assert parse_version_needs(output)["GLIBC"] == {"2.34"}


def test_audit_accepts_pinned_build_host_valid_png_and_compatible_elf(tmp_path: Path) -> None:
    root, entrypoint, manifest, icon = _fixture(tmp_path)

    report = _audit(
        root=root,
        entrypoint=entrypoint,
        manifest=manifest,
        icon=icon,
    )

    assert report["passed"] is True
    assert report["elf_file_count"] == 1
    assert report["highest_requirements"]["GLIBC"] == "2.34"
    assert report["source_revision"] == SOURCE_REVISION
    assert report["build_flags"] == [f"project_version={PROJECT_VERSION}"]


@pytest.mark.parametrize(
    ("family", "version", "kwargs"),
    (
        ("GLIBC", "2.43", {"glibc": "2.43"}),
        ("GLIBCXX", "3.4.35", {"glibcxx": "3.4.35"}),
        ("CXXABI", "1.3.16", {"cxxabi": "1.3.16"}),
    ),
)
def test_audit_rejects_newer_abi_requirement(
    tmp_path: Path,
    family: str,
    version: str,
    kwargs: dict[str, str],
) -> None:
    root, entrypoint, manifest, icon = _fixture(tmp_path)
    readelf = tmp_path / "readelf"
    _write_readelf(readelf, **kwargs)

    report = _audit(
        root=root,
        entrypoint=entrypoint,
        manifest=manifest,
        icon=icon,
    )

    assert report["passed"] is False
    assert any(f"{family}_{version}" in error for error in report["errors"])


def test_audit_rejects_wrong_build_host_hash_and_invalid_png(tmp_path: Path) -> None:
    root, entrypoint, manifest, icon = _fixture(tmp_path)
    _write_manifest(
        manifest,
        entrypoint,
        tmp_path / "build_nuitka_fast.sh",
        distro_version_id="rolling",
    )
    entrypoint.write_bytes(elf64_header() + b"changed")
    icon.write_bytes(b"png")

    report = _audit(
        root=root,
        entrypoint=entrypoint,
        manifest=manifest,
        icon=icon,
    )

    assert report["passed"] is False
    assert any("distro_version_id" in error for error in report["errors"])
    assert any("artifact_sha256" in error for error in report["errors"])
    assert any("not a PNG" in error for error in report["errors"])


def test_audit_rejects_aarch64_elf_in_x86_64_payload(tmp_path: Path) -> None:
    root, entrypoint, manifest, icon = _fixture(tmp_path)
    arm_library = root / "maps-native.so"
    arm_library.write_bytes(elf64_header(machine=183) + b"arm")
    _write_manifest(manifest, entrypoint, tmp_path / "build_nuitka_fast.sh")

    report = _audit(
        root=root,
        entrypoint=entrypoint,
        manifest=manifest,
        icon=icon,
    )

    assert report["passed"] is False
    assert any("e_machine=183" in error for error in report["errors"])


def test_audit_rejects_non_entrypoint_change_after_manifest_creation(tmp_path: Path) -> None:
    root, entrypoint, manifest, icon = _fixture(tmp_path)
    plugin = root / "qt-plugin.so"
    plugin.write_bytes(elf64_header() + b"original")
    _write_manifest(manifest, entrypoint, tmp_path / "build_nuitka_fast.sh")
    plugin.write_bytes(elf64_header() + b"replacement")

    report = _audit(
        root=root,
        entrypoint=entrypoint,
        manifest=manifest,
        icon=icon,
    )

    assert report["passed"] is False
    assert any("artifact_tree_sha256" in error for error in report["errors"])


def test_flatpak_rejects_stale_standalone_from_different_revision(tmp_path: Path) -> None:
    root, entrypoint, manifest, icon = _fixture(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["source_revision"] = "older-revision"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report = _audit(
        root=root,
        entrypoint=entrypoint,
        manifest=manifest,
        icon=icon,
    )

    assert report["passed"] is False
    assert any("source_revision" in error for error in report["errors"])


def test_audit_rejects_stale_project_version_and_build_driver(tmp_path: Path) -> None:
    root, entrypoint, manifest, icon = _fixture(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["environment"]["build_flags"] = ["project_version=6.6.7"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "build_nuitka_fast.sh").write_text("changed\n", encoding="utf-8")

    report = _audit(
        root=root,
        entrypoint=entrypoint,
        manifest=manifest,
        icon=icon,
    )

    assert report["passed"] is False
    assert any("project_version" in error for error in report["errors"])
    assert any("build_driver_sha256" in error for error in report["errors"])


def test_artifact_tree_hash_rejects_executable_mode_change(tmp_path: Path) -> None:
    root, entrypoint, manifest, icon = _fixture(tmp_path)
    helper = root / "osmand_render_helper"
    helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper.chmod(0o755)
    _write_manifest(manifest, entrypoint, tmp_path / "build_nuitka_fast.sh")
    helper.chmod(0o644)

    report = _audit(
        root=root,
        entrypoint=entrypoint,
        manifest=manifest,
        icon=icon,
    )

    assert report["passed"] is False
    assert any("artifact_tree_sha256" in error for error in report["errors"])


def test_artifact_tree_hash_rejects_symlink_target_change(tmp_path: Path) -> None:
    root, entrypoint, manifest, icon = _fixture(tmp_path)
    (root / "first.dat").write_text("same", encoding="utf-8")
    (root / "second.dat").write_text("same", encoding="utf-8")
    link = root / "current.dat"
    try:
        link.symlink_to("first.dat")
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    _write_manifest(manifest, entrypoint, tmp_path / "build_nuitka_fast.sh")
    link.unlink()
    link.symlink_to("second.dat")

    report = _audit(
        root=root,
        entrypoint=entrypoint,
        manifest=manifest,
        icon=icon,
    )

    assert report["passed"] is False
    assert any("artifact_tree_sha256" in error for error in report["errors"])


def test_audit_rejects_legacy_manifest_without_build_host(tmp_path: Path) -> None:
    root, entrypoint, manifest, icon = _fixture(tmp_path)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_path": entrypoint.name,
                "artifact_sha256": hashlib.sha256(entrypoint.read_bytes()).hexdigest(),
                "environment": {},
            }
        ),
        encoding="utf-8",
    )

    report = _audit(
        root=root,
        entrypoint=entrypoint,
        manifest=manifest,
        icon=icon,
    )

    assert report["passed"] is False
    assert "standalone build manifest has no build_host provenance" in report["errors"]
