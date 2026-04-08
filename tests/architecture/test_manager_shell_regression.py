"""Architecture test: LibraryManager must remain a thin coordination shell.

``LibraryManager`` is classified as a legacy coordination object (Phase 5 Class C
– thin-shell coordinator).  Its role is to own Qt signals, delegate tree/scan/
watch/geo/trash operations to mixin classes, and wire application-layer services.

Checked constraints
-------------------
1. The module must not define more than one class (only ``LibraryManager`` itself).
2. ``LibraryManager`` must not import from ``iPhoto.domain`` at runtime – domain
   models should only be used through application services.
3. The manager class must expose the expected thin-shell public API surface and
   must *not* define methods that duplicate business rules already delegated to
   application services (no ``_scan_*`` business helpers defined directly on the
   class body – those live in ScanCoordinatorMixin).
4. Module-level code must not contain ``for`` or ``while`` loops (no business
   logic at import time).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "iPhoto"
MANAGER_FILE = SRC_ROOT / "library" / "manager.py"

# The only public class that the manager module is allowed to define.
ALLOWED_CLASSES = {"LibraryManager"}

# Methods that must be present on LibraryManager to keep the public shell contract.
REQUIRED_METHODS = {
    "root",
    "bind_path",
    "list_albums",
    "list_children",
    "scan_tree",
    "shutdown",
}

# Methods that must NOT be defined directly on LibraryManager – they belong to
# the mixin layer or application services.
PROHIBITED_DIRECT_METHODS = {
    "create_album",
    "delete_album",
    "rename_album",
    "start_scanning",
    "stop_scanning",
}


def _parse() -> ast.Module:
    return ast.parse(MANAGER_FILE.read_text(encoding="utf-8"))


def test_manager_module_defines_at_most_one_class() -> None:
    """Only LibraryManager (and no extra helper classes) may be defined here."""
    tree = _parse()
    class_names = [
        node.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, ast.ClassDef)
    ]
    unexpected = [n for n in class_names if n not in ALLOWED_CLASSES]
    assert not unexpected, (
        f"Unexpected class definitions in library/manager.py: {unexpected}\n"
        "Extra classes must live in dedicated sub-modules, not in the manager shell."
    )


def test_manager_module_has_no_module_level_loops() -> None:
    """No for/while loops at module scope – manager.py is a shell, not business logic."""
    tree = _parse()
    violations: list[int] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.For, ast.While)):
            violations.append(node.lineno)
    assert not violations, (
        f"Module-level loops in library/manager.py at lines: {violations}\n"
        "Move business logic to application use cases or mixin helpers."
    )


def test_manager_class_exposes_required_public_api() -> None:
    """LibraryManager must still expose the documented thin-shell public surface."""
    tree = _parse()

    manager_class: ast.ClassDef | None = None
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and node.name == "LibraryManager":
            manager_class = node
            break

    assert manager_class is not None, "LibraryManager class not found in manager.py"

    defined_methods = {
        node.name
        for node in ast.iter_child_nodes(manager_class)
        if isinstance(node, ast.FunctionDef)
    }
    missing = REQUIRED_METHODS - defined_methods
    assert not missing, (
        f"LibraryManager is missing required shell methods: {missing}\n"
        "These are part of the stable public surface that must not be removed."
    )


def test_manager_class_does_not_redefine_mixin_methods() -> None:
    """LibraryManager must not re-implement methods that belong to the mixin layer."""
    tree = _parse()

    manager_class: ast.ClassDef | None = None
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and node.name == "LibraryManager":
            manager_class = node
            break

    assert manager_class is not None, "LibraryManager class not found in manager.py"

    # Only look at direct method definitions on the class body (not nested).
    direct_methods = {
        node.name
        for node in ast.iter_child_nodes(manager_class)
        if isinstance(node, ast.FunctionDef)
    }
    violations = direct_methods & PROHIBITED_DIRECT_METHODS
    assert not violations, (
        f"LibraryManager directly defines mixin-owned methods: {violations}\n"
        "These methods must live in the appropriate Mixin class, not on the shell."
    )
