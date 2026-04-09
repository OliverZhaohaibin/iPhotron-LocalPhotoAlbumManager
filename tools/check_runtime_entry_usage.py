#!/usr/bin/env python3
"""Check that runtime AppContext imports stay confined to the shim itself."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ALLOWED_SUBTREES = [
    "appctx.py",
]


def _is_type_checking_guard(node: ast.If) -> bool:
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


def _runtime_appctx_imports(source: str) -> list[int]:
    tree = ast.parse(source)
    violations: list[int] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._in_type_checking = False

        def visit_If(self, node: ast.If) -> None:
            previous = self._in_type_checking
            if _is_type_checking_guard(node):
                self._in_type_checking = True
                for child in node.body:
                    self.visit(child)
                self._in_type_checking = previous
                for child in node.orelse:
                    self.visit(child)
                return
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            if self._in_type_checking:
                return
            for alias in node.names:
                if alias.name == "appctx" or alias.name.endswith(".appctx"):
                    violations.append(node.lineno)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if self._in_type_checking:
                return
            module = node.module or ""
            imports_appctx_module = module in {"iPhoto", ""}
            imports_from_appctx = module == "appctx" or module.endswith(".appctx")
            for alias in node.names:
                if imports_appctx_module and alias.name == "appctx":
                    violations.append(node.lineno)
                elif imports_from_appctx and alias.name in {"AppContext", "*"}:
                    violations.append(node.lineno)

    Visitor().visit(tree)
    return violations


def _is_allowed(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    return any(normalized.startswith(prefix) for prefix in ALLOWED_SUBTREES)


def check(src_root: Path) -> list[str]:
    violations: list[str] = []
    for py_file in sorted(src_root.rglob("*.py")):
        rel = str(py_file.relative_to(src_root))
        if _is_allowed(rel):
            continue
        source = py_file.read_text(encoding="utf-8")
        try:
            for lineno in _runtime_appctx_imports(source):
                violations.append(f"{py_file}:{lineno}")
        except SyntaxError as exc:
            violations.append(f"{py_file}: PARSE_ERROR - {exc}")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--src",
        default=str(Path(__file__).parent.parent / "src" / "iPhoto"),
        help="Root of the iPhoto source tree to scan.",
    )
    args = parser.parse_args(argv)

    src_root = Path(args.src)
    if not src_root.exists():
        print(f"ERROR: source directory not found: {src_root}", file=sys.stderr)
        return 2

    found = check(src_root)
    if found:
        print("AppContext imported at runtime outside the compatibility shim:\n")
        for violation in found:
            print(f"  {violation}")
        print(
            "\nFix: use RuntimeContext/RuntimeEntryContract or guard the import with TYPE_CHECKING.",
            file=sys.stderr,
        )
        return 1

    print("OK - no runtime AppContext imports found outside appctx.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
