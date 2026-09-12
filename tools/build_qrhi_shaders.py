"""Rebuild the packaged QRhi shader assets with the production target set."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

_SHADER_NAMES = (
    "image_viewer_rhi.vert",
    "image_viewer_rhi.frag",
    "image_viewer_overlay.vert",
    "image_viewer_overlay.frag",
    "video_renderer.vert",
    "video_renderer.frag",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qsb", help="Path to Qt Shader Baker; defaults to PATH lookup")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Bake into a temporary directory and compare with committed assets",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    shader_dir = repo_root / "src" / "iPhoto" / "gui" / "ui" / "widgets"
    qsb = args.qsb or shutil.which("qsb")
    if not qsb:
        raise SystemExit("Qt Shader Baker (qsb) was not found")

    output_dir = shader_dir
    temporary = None
    if args.check:
        import tempfile

        temporary = tempfile.TemporaryDirectory(prefix="iphoto-qsb-")
        output_dir = Path(temporary.name)

    try:
        for name in _SHADER_NAMES:
            source = shader_dir / name
            output = output_dir / f"{name}.qsb"
            subprocess.run(  # noqa: S603 - qsb is invoked without a shell
                [
                    qsb,
                    "--glsl",
                    "150",
                    "--hlsl",
                    "50",
                    "--msl",
                    "12",
                    "-o",
                    str(output),
                    str(source),
                ],
                check=True,
            )
            if args.check and output.read_bytes() != (shader_dir / output.name).read_bytes():
                raise SystemExit(f"Stale QRhi shader asset: {output.name}")
    finally:
        if temporary is not None:
            temporary.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
