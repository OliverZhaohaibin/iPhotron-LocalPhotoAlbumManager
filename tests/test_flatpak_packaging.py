from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

APP_ID = "io.github.oliverzhaohaibin.iPhotron"
LEGACY_APP_ID = "com.github.OliverZhaohaibin.iPhotron"


def _executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _png(path: Path) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    width = height = 256
    scanlines = b"".join(b"\x00" + (b"\x00\x00\x00\xff" * width) for _ in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )


def _fake_standalone(root: Path) -> Path:
    standalone = root / "entrypoint.dist"
    (standalone / "iPhoto/resources/i18n").mkdir(parents=True)
    (standalone / "iPhoto/gui/ui/widgets").mkdir(parents=True)
    (standalone / "maps/tiles/extension/bin").mkdir(parents=True)
    (standalone / "PySide6/Qt/plugins/platforms").mkdir(parents=True)
    entrypoint = standalone / "entrypoint.bin"
    entrypoint.write_bytes(b"\x7fELFflatpak-test")
    entrypoint.chmod(0o755)
    (standalone / "iPhoto/gui/ui/widgets/image.qsb").write_bytes(b"qsb")
    _executable(
        standalone / "maps/tiles/extension/bin/osmand_render_helper",
        "#!/bin/sh\nexit 0\n",
    )
    (standalone / "PySide6/Qt/plugins/platforms/libqxcb.so").write_bytes(b"qt")
    return standalone


def _standalone_manifest(path: Path, entrypoint: Path) -> Path:
    manifest = path / "build-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_path": entrypoint.name,
                "artifact_sha256": hashlib.sha256(entrypoint.read_bytes()).hexdigest(),
                "environment": {
                    "build_host": {
                        "system": "Linux",
                        "machine": "x86_64",
                        "distro_id": "ubuntu",
                        "distro_version_id": "24.04",
                        "libc_name": "glibc",
                        "libc_version": "2.39",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_flatpak_manifest_declares_runtime_identity_and_required_permissions() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    manifest = (
        repo_root
        / "packaging/flatpak/io.github.oliverzhaohaibin.iPhotron.yml"
    ).read_text(encoding="utf-8")

    assert f"id: {APP_ID}" in manifest
    assert "app-id:" not in manifest
    assert "runtime: org.freedesktop.Platform" in manifest
    assert "runtime-version: '25.08'" in manifest
    assert "sdk: org.freedesktop.Sdk" in manifest
    assert "command: iphoto" in manifest
    assert "dest: payload" in manifest
    for permission in (
        "--socket=wayland",
        "--socket=fallback-x11",
        "--device=dri",
        "--socket=pulseaudio",
        "--share=network",
        "--filesystem=host:rw",
    ):
        assert permission in manifest

    launcher = (repo_root / "packaging/flatpak/iphotron-launcher").read_text(
        encoding="utf-8"
    )
    assert "APP_ROOT=/app/lib/iphotron" in launcher
    assert "entrypoint.bin entrypoint main.bin main" in launcher

    metadata_path = (
        repo_root
        / "packaging/flatpak/io.github.oliverzhaohaibin.iPhotron.metainfo.xml"
    )
    metadata = ET.parse(metadata_path).getroot()
    assert metadata.findtext("id") == APP_ID
    assert metadata.findtext("launchable") == f"{APP_ID}.desktop"
    assert metadata.findtext("provides/id") == LEGACY_APP_ID
    assert metadata.findtext("replaces/id") == LEGACY_APP_ID

    desktop = (
        repo_root / f"packaging/flatpak/{APP_ID}.desktop"
    ).read_text(encoding="utf-8")
    assert "Exec=iphoto" in desktop
    assert f"Icon={APP_ID}" in desktop


def test_flatpak_wrapper_orchestrates_staging_and_writes_reports(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    standalone = _fake_standalone(tmp_path)
    standalone_manifest = _standalone_manifest(tmp_path, standalone / "entrypoint.bin")
    icon = tmp_path / "iphoto.png"
    _png(icon)
    output = tmp_path / f"{APP_ID}-6.6.8-x86_64.flatpak"

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "uname",
        '#!/bin/sh\nif [ "${1:-}" = "-m" ]; then echo x86_64; else echo Linux; fi\n',
    )
    builder_record = tmp_path / "flatpak-builder-record.txt"
    _executable(
        fake_bin / "flatpak-builder",
        """#!/bin/sh
for argument in "$@"; do manifest="$argument"; done
context="$(dirname "$manifest")"
test -x "$context/payload/entrypoint.bin"
test -f "$context/iphotron.png"
test -f "$context/io.github.oliverzhaohaibin.iPhotron.desktop"
grep -q 'path: payload' "$manifest"
printf '%s\n' "$@" > "$FLATPAK_TEST_RECORD"
""",
    )
    _executable(
        fake_bin / "readelf",
        """#!/bin/sh
cat <<'EOF'
Version needs section '.gnu.version_r' contains 1 entry:
  0x0010: Name: GLIBC_2.34 Flags: none Version: 2
EOF
""",
    )
    _executable(
        fake_bin / "flatpak",
        """#!/bin/sh
if [ "$1" = "--default-arch" ]; then
  echo x86_64
  exit 0
fi
if [ "$1" = "build-bundle" ]; then
  touch "$5"
  exit 0
fi
exit 2
""",
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FLATPAK_TEST_RECORD"] = str(builder_record)

    command = [
        "bash",
        str(repo_root / "scripts/build_flatpak.sh"),
        "--standalone-dir",
        str(standalone),
        "--standalone-manifest",
        str(standalone_manifest),
        "--icon",
        str(icon),
        "--output",
        str(output),
    ]
    result = subprocess.run(
        command,
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert Path(f"{output}.abi-report.json").is_file()
    assert Path(f"{output}.build-manifest.json").is_file()
    builder_args = builder_record.read_text(encoding="utf-8")
    assert "--user" in builder_args
    assert "--install-deps-from=flathub" in builder_args
    assert "--default-branch=6.6.8" in builder_args

    repeated = subprocess.run(
        command,
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert repeated.returncode == 2
    assert "refusing to overwrite" in repeated.stderr
