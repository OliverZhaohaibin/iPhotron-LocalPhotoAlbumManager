#!/usr/bin/env python3
"""Run the architecture checks used by the runtime-entry refactor."""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TOOLS_DIR))

import check_runtime_entry_usage  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    src_root = str(Path(__file__).parent.parent / "src" / "iPhoto")
    return check_runtime_entry_usage.main(["--src", src_root])


if __name__ == "__main__":
    sys.exit(main())
