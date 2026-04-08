#!/usr/bin/env python3
"""Unified architecture check entry point.

Runs all architecture-level static checks in sequence and returns a single
aggregated exit code so CI only needs one command:

    python tools/check_architecture.py

Individual checks can still be run directly; this script is just a convenience
wrapper that:

1. Runs ``check_runtime_entry_usage.py`` – AppContext must not be imported at
   runtime outside the allowed legacy paths.
2. Runs ``check_adapter_boundary.py`` – adapter modules must not directly
   import from the infrastructure layer.

Exit codes
----------
0   All checks passed.
1   One or more checks detected violations.
2   An internal error occurred (bad argument, missing file, …).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running this script from any working directory.
_TOOLS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TOOLS_DIR))

import check_adapter_boundary  # noqa: E402
import check_runtime_entry_usage  # noqa: E402

_SECTION_WIDTH = 60


def _banner(title: str) -> None:
    print(f"\n{'─' * _SECTION_WIDTH}")
    print(f"  {title}")
    print(f"{'─' * _SECTION_WIDTH}")


def main(argv: list[str] | None = None) -> int:
    """Run all architecture checks and return a unified exit code."""

    src_root = str(Path(__file__).parent.parent / "src" / "iPhoto")

    results: list[tuple[str, int]] = []

    _banner("Check 1 / 2 — AppContext runtime import boundary")
    code = check_runtime_entry_usage.main(["--src", src_root])
    results.append(("AppContext runtime import boundary", code))

    _banner("Check 2 / 2 — Adapter → Infrastructure boundary")
    code = check_adapter_boundary.main(["--src", src_root])
    results.append(("Adapter → Infrastructure boundary", code))

    print(f"\n{'═' * _SECTION_WIDTH}")
    print("  Architecture check summary")
    print(f"{'═' * _SECTION_WIDTH}")

    any_violation = False
    any_internal_error = False
    for name, rc in results:
        if rc == 2:
            status = "ERROR !"
            any_internal_error = True
        elif rc != 0:
            status = "FAIL ✗"
            any_violation = True
        else:
            status = "PASS ✓"
        print(f"  [{status}]  {name}")

    print(f"{'═' * _SECTION_WIDTH}\n")

    if any_internal_error:
        print("Architecture checks encountered internal errors — see details above.", file=sys.stderr)
        return 2

    if any_violation:
        print("Architecture checks FAILED — see details above.", file=sys.stderr)
        return 1

    print("All architecture checks PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
