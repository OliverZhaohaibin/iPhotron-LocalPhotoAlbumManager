"""Architecture regression: vNext layer boundaries stay enforced."""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"
SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "iPhoto"

sys.path.insert(0, str(TOOLS_DIR))

import check_layer_boundaries  # noqa: E402


def test_vnext_layer_boundaries() -> None:
    violations = check_layer_boundaries.check(SRC_ROOT)

    assert not violations, (
        "vNext layer boundary violations:\n"
        + "\n".join(f"  {item}" for item in violations)
    )
