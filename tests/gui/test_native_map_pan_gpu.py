"""Opt-in native marker synchronization tests on a macOS desktop."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.gpu
@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("IPHOTO_RUN_MACOS_GPU_TESTS") != "1",
    reason="requires a macOS desktop and native map extension",
)
@pytest.mark.parametrize("scale", ["0.5", "1"])
def test_native_markers_follow_projection(scale, tmp_path):
    env = os.environ.copy()
    env.update(
        {
            "QT_QPA_PLATFORM": "cocoa",
            "QT_SCALE_FACTOR": scale,
            "IPHOTO_RHI_BACKEND": "metal",
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
        }
    )
    env.pop("IPHOTO_DISABLE_OPENGL", None)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("native_map_pan_probe.py"))],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout[-1200:] + "\n" + result.stderr[-5000:]
    assert "PAN_RESULT=" in result.stdout
    print(next(line for line in result.stdout.splitlines() if line.startswith("PAN_RESULT=")))
