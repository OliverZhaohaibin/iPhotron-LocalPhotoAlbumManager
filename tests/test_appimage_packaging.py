from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize("override_icon", [False, True], ids=["default-icon", "custom-icon"])
def test_appimage_builder_stages_and_validates_delivery_bundle(tmp_path, override_icon) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    standalone = tmp_path / "entrypoint.dist"
    (standalone / "iPhoto/gui/ui/widgets").mkdir(parents=True)
    (standalone / "maps/tiles/extension/bin").mkdir(parents=True)
    (standalone / "PySide6/Qt/plugins/platforms").mkdir(parents=True)
    (standalone / "onnxruntime/capi").mkdir(parents=True)
    (standalone / "onnxruntime/capi/onnxruntime_pybind11_state.so").write_bytes(b"ORT fixture")
    _executable(standalone / "entrypoint.bin", "#!/bin/sh\nexit 0\n")
    (standalone / "iPhoto/gui/ui/widgets/image.qsb").write_bytes(b"qsb")
    _executable(
        standalone / "maps/tiles/extension/bin/osmand_render_helper",
        "#!/bin/sh\nexit 0\n",
    )
    (standalone / "PySide6/Qt/plugins/platforms/libqxcb.so").write_bytes(b"qt")
    icon = repo_root / "logo.png"
    icon_args = []
    if override_icon:
        icon = tmp_path / "custom icon.png"
        icon.write_bytes(b"png")
        icon_args = ["--icon", str(icon)]

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
            *icon_args,
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
    assert Path(f"{output}.build-manifest.json").is_file()
    appdir = tmp_path / "iPhotron.AppDir"
    assert (appdir / "iphoto.png").read_bytes() == icon.read_bytes()
    assert (appdir / "AppRun").stat().st_mode & 0o111
    assert (appdir / "usr/bin/entrypoint.bin").stat().st_mode & 0o111
    assert (appdir / "maps").exists() is False
    assert (appdir / "usr/bin/maps/tiles").is_dir()
    launch = subprocess.run(
        [str(appdir / "AppRun")],
        cwd=appdir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert launch.returncode == 0, launch.stderr
