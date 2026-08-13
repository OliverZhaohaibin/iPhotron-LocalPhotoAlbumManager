from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(script: str) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_documentation_links() -> None:
    _run("check_docs_links.py")


def test_readme_localization_parity() -> None:
    _run("check_readme_parity.py")
