from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

APP_ID = "com.github.OliverZhaohaibin.iPhotron"


def _executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _fake_standalone(root: Path) -> Path:
    standalone = root / "entrypoint.dist"
    (standalone / "iPhoto/resources/i18n").mkdir(parents=True)
    (standalone / "iPhoto/gui/ui/widgets").mkdir(parents=True)
    (standalone / "maps/tiles/extension/bin").mkdir(parents=True)
    (standalone / "PySide6/Qt/plugins/platforms").mkdir(parents=True)
    _executable(standalone / "entrypoint.bin", "#!/bin/sh\nexit 0\n")
    (standalone / "iPhoto/gui/ui/widgets/image.qsb").write_bytes(b"qsb")
    _executable(
        standalone / "maps/tiles/extension/bin/osmand_render_helper",
        "#!/bin/sh\nexit 0\n",
    )
    (standalone / "PySide6/Qt/plugins/platforms/libqxcb.so").write_bytes(b"qt")
    return standalone


def test_flatpak_manifest_declares_runtime_identity_and_required_permissions() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    manifest = (
        repo_root
        / "packaging/flatpak/com.github.OliverZhaohaibin.iPhotron.yml"
    ).read_text(encoding="utf-8")

    assert f"app-id: {APP_ID}" in manifest
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
        / "packaging/flatpak/com.github.OliverZhaohaibin.iPhotron.metainfo.xml"
    )
    metadata = ET.parse(metadata_path).getroot()
    assert metadata.findtext("id") == APP_ID
    assert metadata.findtext("launchable") == f"{APP_ID}.desktop"

    desktop = (
        repo_root / f"packaging/flatpak/{APP_ID}.desktop"
    ).read_text(encoding="utf-8")
    assert "Exec=iphoto" in desktop
    assert f"Icon={APP_ID}" in desktop


def test_flatpak_builder_stages_standalone_and_writes_delivery_manifest(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    standalone = _fake_standalone(tmp_path)
    icon = tmp_path / "iphoto.png"
    icon.write_bytes(b"png")
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
test -f "$context/com.github.OliverZhaohaibin.iPhotron.desktop"
grep -q 'path: payload' "$manifest"
printf '%s\n' "$@" > "$FLATPAK_TEST_RECORD"
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
    assert Path(f"{output}.build-manifest.json").is_file()
    builder_args = builder_record.read_text(encoding="utf-8")
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
