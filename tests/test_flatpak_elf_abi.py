from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest

from tools.check_flatpak_elf_abi import audit_payload, parse_version_needs

LIMITS = {
    "GLIBC": "2.42",
    "GLIBCXX": "3.4.34",
    "CXXABI": "1.3.15",
}


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


def _write_manifest(path: Path, entrypoint: Path, **host_overrides: str) -> None:
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
                "artifact_path": entrypoint.name,
                "artifact_sha256": hashlib.sha256(entrypoint.read_bytes()).hexdigest(),
                "environment": {"build_host": build_host},
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
    entrypoint.write_bytes(b"\x7fELFfixture")
    entrypoint.chmod(0o755)
    manifest = tmp_path / "build-manifest.json"
    _write_manifest(manifest, entrypoint)
    icon = tmp_path / "icon.png"
    write_png(icon)
    readelf = tmp_path / "readelf"
    _write_readelf(readelf)
    return root, entrypoint, manifest, icon


def test_parse_version_needs_ignores_version_definitions() -> None:
    output = """Version needs section '.gnu.version_r' contains 1 entry:
  0x0010: Name: GLIBC_2.34 Flags: none
Version definition section '.gnu.version_d' contains 1 entry:
  0x0020: Name: GLIBC_9.99 Flags: none
"""
    assert parse_version_needs(output)["GLIBC"] == {"2.34"}


def test_audit_accepts_pinned_build_host_valid_png_and_compatible_elf(tmp_path: Path) -> None:
    root, entrypoint, manifest, icon = _fixture(tmp_path)
    readelf = tmp_path / "readelf"

    report = audit_payload(
        root=root,
        entrypoint=entrypoint,
        build_manifest=manifest,
        icon=icon,
        limits=LIMITS,
        readelf_bin=str(readelf),
    )

    assert report["passed"] is True
    assert report["elf_file_count"] == 1
    assert report["highest_requirements"]["GLIBC"] == "2.34"


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

    report = audit_payload(
        root=root,
        entrypoint=entrypoint,
        build_manifest=manifest,
        icon=icon,
        limits=LIMITS,
        readelf_bin=str(readelf),
    )

    assert report["passed"] is False
    assert any(f"{family}_{version}" in error for error in report["errors"])


def test_audit_rejects_wrong_build_host_hash_and_invalid_png(tmp_path: Path) -> None:
    root, entrypoint, manifest, icon = _fixture(tmp_path)
    _write_manifest(manifest, entrypoint, distro_version_id="rolling")
    entrypoint.write_bytes(b"\x7fELFchanged")
    icon.write_bytes(b"png")

    report = audit_payload(
        root=root,
        entrypoint=entrypoint,
        build_manifest=manifest,
        icon=icon,
        limits=LIMITS,
        readelf_bin=str(tmp_path / "readelf"),
    )

    assert report["passed"] is False
    assert any("distro_version_id" in error for error in report["errors"])
    assert any("artifact_sha256" in error for error in report["errors"])
    assert any("not a PNG" in error for error in report["errors"])


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

    report = audit_payload(
        root=root,
        entrypoint=entrypoint,
        build_manifest=manifest,
        icon=icon,
        limits=LIMITS,
        readelf_bin=str(tmp_path / "readelf"),
    )

    assert report["passed"] is False
    assert "standalone build manifest has no build_host provenance" in report["errors"]
