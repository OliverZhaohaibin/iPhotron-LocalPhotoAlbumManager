#!/usr/bin/env python3
"""Check that adapter modules do not directly import from the infrastructure layer.

Adapter modules in ``presentation/qt/adapters/`` and ``gui/services/`` must
communicate with infrastructure *only* through application services.  Direct
imports from ``iPhoto.infrastructure`` in these files violate the boundary.

Usage::

    python tools/check_adapter_boundary.py [--src SRC_DIR]

Exit code 0 means no violations.
Exit code 1 means boundary violations were detected.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ADAPTER_SUBTREES = [
    "presentation/qt/adapters",
    "gui/services",
]

INFRA_PACKAGE = "iPhoto.infrastructure"


def _collect_py_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.rglob("*.py"))


def _is_infra_import(node: ast.ImportFrom | ast.Import) -> bool:
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if module.startswith(INFRA_PACKAGE):
            return True
        parts = module.lstrip(".").split(".")
        if parts and parts[0] == "infrastructure":
            return True
    elif isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith(INFRA_PACKAGE):
                return True
    return False


def _direct_infra_imports(source: str) -> list[int]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    violations: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ImportFrom, ast.Import)):
            if _is_infra_import(node):
                violations.append(node.lineno)
    return violations


def check(src_root: Path) -> list[str]:
    violations: list[str] = []
    for subtree_rel in ADAPTER_SUBTREES:
        subtree = src_root / subtree_rel
        for py_file in _collect_py_files(subtree):
            if py_file.name == "__init__.py":
                continue
            source = py_file.read_text(encoding="utf-8")
            lines = _direct_infra_imports(source)
            for lineno in lines:
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
        print("Adapter boundary violations - direct infrastructure imports detected:\n")
        for v in found:
            print(f"  {v}")
        print(
            "\nFix: route infrastructure calls through application services "
            "(application/services/ or application/use_cases/).",
            file=sys.stderr,
        )
        return 1

    print("OK - no adapter->infrastructure boundary violations found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
