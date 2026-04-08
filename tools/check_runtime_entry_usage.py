#!/usr/bin/env python3
"""Check that no new code imports AppContext outside allowed legacy paths.

Usage::

    python tools/check_runtime_entry_usage.py [--src SRC_DIR]

Exit code 0 means no violations were found.
Exit code 1 means one or more violations were detected.

Allowed paths (runtime AppContext imports are expected/accepted here):
- src/iPhoto/appctx.py          (defines the shim)
- src/iPhoto/app.py             (legacy shim - Class B)
- src/iPhoto/gui/               (legacy GUI layer)
- tests/                        (test fixtures may construct AppContext)
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ALLOWED_SUBTREES = [
    "appctx.py",
    "app.py",
    "gui/",
    "bootstrap/",
]

VIOLATION_REPORT_HEADER = (
    "AppContext imported at runtime (outside TYPE_CHECKING) in non-legacy code:\n"
)


def _is_type_checking_guard(node: ast.If) -> bool:
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


def _runtime_appctx_imports(source: str) -> list[int]:
    """Return line numbers of AppContext imports that are NOT type-checking-only."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    violations: list[int] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._in_type_checking: bool = False

        def visit_If(self, node: ast.If) -> None:
            is_tc = _is_type_checking_guard(node)
            prev = self._in_type_checking
            self._in_type_checking = self._in_type_checking or is_tc
            self.generic_visit(node)
            self._in_type_checking = prev

        def visit_Import(self, node: ast.Import) -> None:
            if self._in_type_checking:
                return
            for alias in node.names:
                if "appctx" in alias.name:
                    violations.append(node.lineno)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if self._in_type_checking:
                return
            module = node.module or ""
            if "appctx" in module:
                for alias in node.names:
                    if alias.name == "AppContext":
                        violations.append(node.lineno)

    Visitor().visit(tree)
    return violations


def _is_allowed(rel_path: str) -> bool:
    return any(rel_path.replace("\\", "/").startswith(sub) for sub in ALLOWED_SUBTREES)


def check(src_root: Path) -> list[str]:
    violations: list[str] = []
    for py_file in sorted(src_root.rglob("*.py")):
        rel = str(py_file.relative_to(src_root))
        if _is_allowed(rel):
            continue
        source = py_file.read_text(encoding="utf-8")
        for lineno in _runtime_appctx_imports(source):
            violations.append(f"{py_file}:{lineno}")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--src",
        default=str(Path(__file__).parent.parent / "src" / "iPhoto"),
        help="Root of the iPhoto source tree to scan (default: src/iPhoto)",
    )
    args = parser.parse_args(argv)

    src_root = Path(args.src)
    if not src_root.exists():
        print(f"ERROR: source directory not found: {src_root}", file=sys.stderr)
        return 2

    found = check(src_root)
    if found:
        print(VIOLATION_REPORT_HEADER)
        for v in found:
            print(f"  {v}")
        print(
            "\nFix: guard the import with 'if TYPE_CHECKING:' or migrate to RuntimeContext.",
            file=sys.stderr,
        )
        return 1

    print("OK - no AppContext runtime imports found outside allowed paths.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
