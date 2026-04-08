"""Architecture test: shim files must not contain business logic.

``app.py`` is a deprecated-only shim (Phase 4 Class B).  Its only valid
content is delegation to application-layer use cases via simple forwarding
calls.  Business rules such as loops (``for``/``while``), complex conditionals,
or in-place data transformations must not appear at module or class scope.

Checked constraints
-------------------
1. No ``for`` or ``while`` loop statements at module scope.
2. No ``while`` loops inside the shim's top-level function bodies.
3. No ``for`` loops inside the shim's top-level function bodies.
4. The module does not define any ``class`` (Phase 5 Rule C: no class
   definitions are permitted in the deprecated shim).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "iPhoto"

SHIM_FILE = SRC_ROOT / "app.py"


def _parse_shim() -> ast.Module:
    source = SHIM_FILE.read_text(encoding="utf-8")
    return ast.parse(source)


def test_shim_has_no_module_level_loops() -> None:
    """app.py must not have for/while loops at module scope."""
    tree = _parse_shim()
    violations: list[int] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.For, ast.While)):
            violations.append(node.lineno)

    assert not violations, (
        f"Module-level loops found in app.py at lines: {violations}\n"
        "app.py is a shim - it must not contain business logic loops."
    )


def test_shim_functions_have_no_while_loops() -> None:
    """Top-level functions in app.py must not use while loops."""
    tree = _parse_shim()
    violations: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.While):
                violations.append(f"{node.name}:{child.lineno}")

    assert not violations, (
        f"while loops found inside shim functions: {violations}\n"
        "app.py is a delegation shim - use application use cases for loops."
    )


def test_shim_functions_are_single_delegation() -> None:
    """Each top-level function in app.py should be a thin delegator.

    Allowed: a single import block followed by a single return/call expression.
    Violation: any ``for`` loop inside a function body.
    """
    tree = _parse_shim()
    violations: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.For):
                violations.append(f"{node.name}:{child.lineno}")

    assert not violations, (
        f"for loops found inside shim functions: {violations}\n"
        "app.py must delegate to use cases rather than implementing loops."
    )


def test_shim_defines_no_classes() -> None:
    """app.py must not define any class (Phase 5 Rule C: no class definitions in shim)."""
    tree = _parse_shim()
    class_names: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            class_names.append(node.name)

    assert not class_names, (
        f"Classes defined in app.py: {class_names}\n"
        "app.py is a shim - it must not introduce any class definitions."
    )
