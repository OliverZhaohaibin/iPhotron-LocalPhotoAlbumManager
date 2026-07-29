from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="AppImage packaging contracts require a Linux host",
)


def _executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def test_appimage_builder_stages_and_validates_delivery_bundle(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    standalone = tmp_path / "main.dist"
    (standalone / "iPhoto/gui/ui/widgets").mkdir(parents=True)
    (standalone / "maps/tiles/extension/bin").mkdir(parents=True)
    (standalone / "PySide6/Qt/plugins/platforms").mkdir(parents=True)
    _executable(standalone / "main.bin", "#!/bin/sh\nexit 0\n")
    (standalone / "iPhoto/gui/ui/widgets/image.qsb").write_bytes(b"qsb")
    _executable(
        standalone / "maps/tiles/extension/bin/osmand_render_helper",
        "#!/bin/sh\nexit 0\n",
    )
    (standalone / "PySide6/Qt/plugins/platforms/libqxcb.so").write_bytes(b"qt")
    icon = tmp_path / "iphoto.png"
    icon.write_bytes(b"png")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "uname",
        '#!/bin/sh\nif [ "${1:-}" = "-m" ]; then echo x86_64; else echo Linux; fi\n',
    )
    _executable(fake_bin / "appimagetool", '#!/bin/sh\ntouch "$2"\n')
    output = tmp_path / "iPhotron.AppImage"
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

    result = subprocess.run(
        [
            "bash",
            str(repo_root / "scripts/build_appimage.sh"),
            "--standalone-dir",
            str(standalone),
            "--icon",
            str(icon),
            "--output",
            str(output),
        ],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output.exists()
    appdir = tmp_path / "iPhotron.AppDir"
    assert (appdir / "AppRun").stat().st_mode & 0o111
    assert (appdir / "usr/bin/main.bin").stat().st_mode & 0o111
    assert (appdir / "maps").exists() is False
    assert (appdir / "usr/bin/maps/tiles").is_dir()
