# ruff: noqa: S101

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "run_pytest_ci.py"


def _run_isolated_pytest(test_file: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return subprocess.run(  # noqa: S603 - command uses the current interpreter and repo runner
        [sys.executable, str(RUNNER), str(test_file), "-q"],
        cwd=test_file.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_ci_pytest_runner_preserves_success_and_failure_exit_codes(tmp_path: Path) -> None:
    passing_test = tmp_path / "test_passing.py"
    passing_test.write_text("def test_passing():\n    assert True\n", encoding="utf-8")
    failing_test = tmp_path / "test_failing.py"
    failing_test.write_text("def test_failing():\n    assert False\n", encoding="utf-8")

    passing = _run_isolated_pytest(passing_test)
    failing = _run_isolated_pytest(failing_test)

    assert passing.returncode == 0
    assert "1 passed" in passing.stdout
    assert failing.returncode == 1
    assert "1 failed" in failing.stdout
