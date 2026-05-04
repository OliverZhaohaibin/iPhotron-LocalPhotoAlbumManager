#!/usr/bin/env python3
"""Run the architecture checks used by the runtime-entry refactor."""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TOOLS_DIR))

import check_coordinator_asset_data_source_usage  # noqa: E402
import check_layer_boundaries  # noqa: E402
import check_runtime_entry_usage  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    src_root = str(Path(__file__).parent.parent / "src" / "iPhoto")
    coordinators_root = str(Path(src_root) / "gui" / "coordinators")
    checks = [
        check_runtime_entry_usage.main(["--src", src_root]),
        check_coordinator_asset_data_source_usage.main(["--src", coordinators_root]),
        check_layer_boundaries.main(["--src", src_root]),
    ]
    return 0 if all(code == 0 for code in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
