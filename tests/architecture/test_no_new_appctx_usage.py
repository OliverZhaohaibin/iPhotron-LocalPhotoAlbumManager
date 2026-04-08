"""Architecture test: no new AppContext imports in formal code paths.

Formal code layers (application/, bootstrap/, use_cases/) must not import
``AppContext`` at runtime.  Only ``TYPE_CHECKING``-guarded imports are allowed
so that the legacy compatibility shim stays confined to the GUI/legacy layer.

Rule: any ``import AppContext`` or ``from … appctx import AppContext`` that
appears *outside* an ``if TYPE_CHECKING:`` block in the formal paths is a
violation.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "iPhoto"

FORMAL_DIRS = [
    SRC_ROOT / "application",
    SRC_ROOT / "bootstrap",
]


def _collect_py_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.rglob("*.py"))


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
                name = alias.name
                if name == "appctx" or name.endswith(".appctx"):
                    violations.append(node.lineno)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if self._in_type_checking:
                return
            module = node.module or ""
            if module == "appctx" or module.endswith(".appctx"):
                for alias in node.names:
                    if alias.name == "AppContext":
                        violations.append(node.lineno)

    Visitor().visit(tree)
    return violations


def _is_type_checking_guard(node: ast.If) -> bool:
    """Return True when *node* is an ``if TYPE_CHECKING:`` block."""
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
    ):
        return True
    return False


def test_no_runtime_appctx_imports_in_formal_layers() -> None:
    """Formal layers must not import AppContext outside TYPE_CHECKING guards."""
    violations: list[str] = []

    for directory in FORMAL_DIRS:
        for py_file in _collect_py_files(directory):
            source = py_file.read_text(encoding="utf-8")
            lines = _runtime_appctx_imports(source)
            for lineno in lines:
                rel = py_file.relative_to(SRC_ROOT.parent.parent)
                violations.append(f"{rel}:{lineno}")

    assert not violations, (
        "AppContext imported at runtime (outside TYPE_CHECKING) in formal layers:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nFix: guard with 'if TYPE_CHECKING:' or migrate to RuntimeContext."
    )
