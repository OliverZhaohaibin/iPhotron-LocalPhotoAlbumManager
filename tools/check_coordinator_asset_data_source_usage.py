#!/usr/bin/env python3
"""Check that coordinators do not import collection-store types directly."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


def _imports_collection_store(source: str) -> list[int]:
    tree = ast.parse(source)
    violations: list[int] = []

    class Visitor(ast.NodeVisitor):
        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module = node.module or ""
            if not (
                module.endswith("asset_data_source")
                or module.endswith("gallery_collection_store")
            ):
                return
            for alias in node.names:
                if alias.name in {"AssetDataSource", "GalleryCollectionStore", "*"}:
                    violations.append(node.lineno)

    Visitor().visit(tree)
    return violations


def check(coordinators_root: Path) -> list[str]:
    violations: list[str] = []
    for py_file in sorted(coordinators_root.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            for lineno in _imports_collection_store(source):
                violations.append(f"{py_file}:{lineno}")
        except SyntaxError as exc:
            violations.append(f"{py_file}: PARSE_ERROR - {exc}")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--src",
        default=str(Path(__file__).parent.parent / "src" / "iPhoto" / "gui" / "coordinators"),
        help="Root of the coordinator subtree to scan.",
    )
    args = parser.parse_args(argv)

    coordinators_root = Path(args.src)
    if not coordinators_root.exists():
        print(f"ERROR: source directory not found: {coordinators_root}", file=sys.stderr)
        return 2

    found = check(coordinators_root)
    if found:
        print("Coordinators must not import collection-store types directly:\n")
        for violation in found:
            print(f"  {violation}")
        print(
            "\nFix: construct or manipulate gallery state through the VM + adapter path.",
            file=sys.stderr,
        )
        return 1

    print("OK - coordinators do not import collection-store types directly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
