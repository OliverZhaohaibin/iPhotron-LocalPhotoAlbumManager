"""Architecture regression: AppContext runtime imports stay in the shim only."""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "iPhoto"
ALLOWED = {"appctx.py"}


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


def test_runtime_appctx_imports_are_confined_to_shim() -> None:
    violations: list[str] = []

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        rel = str(py_file.relative_to(SRC_ROOT)).replace("\\", "/")
        if rel in ALLOWED:
            continue
        source = py_file.read_text(encoding="utf-8")
        for lineno in _runtime_appctx_imports(source):
            violations.append(f"{rel}:{lineno}")

    assert not violations, (
        "AppContext imported at runtime outside appctx.py:\n"
        + "\n".join(f"  {item}" for item in violations)
    )
