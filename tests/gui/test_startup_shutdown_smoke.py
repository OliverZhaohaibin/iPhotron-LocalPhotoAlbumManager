from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_application_startup_exits_through_normal_interpreter_shutdown(
    tmp_path: Path,
) -> None:
    """Exercise the real entrypoint without the native-finalizer test runner."""

    env = os.environ.copy()
    env.update(
        {
            "IPHOTO_DISABLE_OPENGL": "1",
            "IPHOTO_STARTUP_BENCHMARK_AUTO_EXIT_MS": "0",
            "NUMBA_DISABLE_JIT": "1",
            "QT_QPA_PLATFORM": "offscreen",
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "iPhoto.gui.main"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, (
        f"application exited with {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
