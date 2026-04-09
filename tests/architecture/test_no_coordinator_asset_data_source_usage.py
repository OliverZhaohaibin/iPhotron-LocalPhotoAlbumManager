"""Architecture regression: coordinators stay behind AssetListViewModel."""

from __future__ import annotations

import ast
from pathlib import Path

COORDINATORS_ROOT = Path(__file__).parent.parent.parent / "src" / "iPhoto" / "gui" / "coordinators"


def _imports_asset_data_source(source: str) -> list[int]:
    tree = ast.parse(source)
    violations: list[int] = []

    class Visitor(ast.NodeVisitor):
        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module = node.module or ""
            if not module.endswith("asset_data_source"):
                return
            for alias in node.names:
                if alias.name in {"AssetDataSource", "*"}:
                    violations.append(node.lineno)

    Visitor().visit(tree)
    return violations


def test_coordinators_do_not_import_asset_data_source() -> None:
    violations: list[str] = []

    for py_file in sorted(COORDINATORS_ROOT.rglob("*.py")):
        rel = str(py_file.relative_to(COORDINATORS_ROOT)).replace("\\", "/")
        source = py_file.read_text(encoding="utf-8")
        for lineno in _imports_asset_data_source(source):
            violations.append(f"{rel}:{lineno}")

    assert not violations, (
        "Coordinators must not import AssetDataSource directly:\n"
        + "\n".join(f"  {item}" for item in violations)
    )
