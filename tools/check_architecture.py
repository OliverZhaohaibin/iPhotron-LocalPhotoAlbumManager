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

Options
-------
--only <id>     Run only the check identified by <id>.
                Accepted values: ``appctx``, ``adapter``.
--verbose       Show full per-file detail even when checks pass.
--src <path>    Override the source root (default: src/iPhoto relative to repo
                root).  Passed through to each individual check.

Exit codes
----------
0   All checks passed.
1   One or more checks detected violations.
2   An internal error occurred (bad argument, missing file, …).

Architecture check categories
------------------------------
CLI static checks (this script)
    Fast, import-free AST / grep scans.  Run before committing.

pytest architecture regression suite
    Tests that lock specific structural invariants.  Run with:
        python -m pytest tests/architecture/ -v

Full integration test suite
    Functional and unit tests covering the whole codebase:
        QT_QPA_PLATFORM=offscreen python -m pytest tests/ --tb=short
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running this script from any working directory.
_TOOLS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TOOLS_DIR))

import check_adapter_boundary  # noqa: E402
import check_runtime_entry_usage  # noqa: E402

_SECTION_WIDTH = 60

# Registry of all available CLI checks.
# Each entry: (id, display_name, callable)
_ALL_CHECKS: list[tuple[str, str, object]] = [
    ("appctx", "AppContext runtime import boundary", check_runtime_entry_usage),
    ("adapter", "Adapter → Infrastructure boundary", check_adapter_boundary),
]


def _banner(title: str, index: int, total: int) -> None:
    print(f"\n{'─' * _SECTION_WIDTH}")
    print(f"  Check {index} / {total} — {title}")
    print(f"{'─' * _SECTION_WIDTH}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_architecture.py",
        description="Unified architecture CLI check entry point.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Run the full architecture regression suite separately:\n"
            "    python -m pytest tests/architecture/ -v\n\n"
            "Run the full test suite:\n"
            "    QT_QPA_PLATFORM=offscreen python -m pytest tests/ --tb=short"
        ),
    )
    parser.add_argument(
        "--only",
        metavar="ID",
        choices=[cid for cid, _, _ in _ALL_CHECKS],
        help="Run only the specified check (%(choices)s).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Show full per-file detail even when checks pass.",
    )
    parser.add_argument(
        "--src",
        metavar="PATH",
        default=None,
        help="Override the iPhoto source root directory.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run all (or a selected) architecture check(s) and return a unified exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    src_root = args.src or str(Path(__file__).parent.parent / "src" / "iPhoto")

    checks_to_run = _ALL_CHECKS
    if args.only:
        checks_to_run = [c for c in _ALL_CHECKS if c[0] == args.only]

    results: list[tuple[str, int]] = []
    total = len(checks_to_run)

    for idx, (cid, display_name, module) in enumerate(checks_to_run, start=1):
        _banner(display_name, idx, total)
        check_argv = ["--src", src_root]
        code = module.main(check_argv)  # type: ignore[attr-defined]
        results.append((display_name, code))

    print(f"\n{'═' * _SECTION_WIDTH}")
    print("  Architecture CLI check summary")
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

    print(f"{'═' * _SECTION_WIDTH}")
    print()
    print("  Next steps:")
    print("    pytest architecture suite  →  python -m pytest tests/architecture/ -v")
    print("    full test suite            →  python -m pytest tests/ --tb=short")
    print(f"{'═' * _SECTION_WIDTH}\n")

    if any_internal_error:
        print("Architecture checks encountered internal errors — see details above.", file=sys.stderr)
        return 2

    if any_violation:
        print("Architecture checks FAILED — see details above.", file=sys.stderr)
        return 1

    print("All architecture CLI checks PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
