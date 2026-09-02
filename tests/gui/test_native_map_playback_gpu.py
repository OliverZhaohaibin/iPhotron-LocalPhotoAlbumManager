"""Opt-in tests: run on a macOS desktop with the built native map extension."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.gpu
@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("IPHOTO_RUN_MACOS_GPU_TESTS") != "1",
    reason="requires macOS GUI and IPHOTO_RUN_MACOS_GPU_TESTS=1",
)
@pytest.mark.parametrize("order", ["location-first", "playback-first"])
def test_native_location_preserves_metal_playback(order, tmp_path):
    env = os.environ.copy()
    env.update({
        "QT_QPA_PLATFORM": "cocoa",
        "IPHOTO_RHI_BACKEND": "metal",
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    })
    env.pop("IPHOTO_DISABLE_OPENGL", None)
    probe = Path(__file__).with_name("native_map_playback_probe.py")
    result = subprocess.run(
        [sys.executable, str(probe), order],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "GPU_RESULT=" in result.stdout
