"""Architecture test: LibraryUpdateService must not accumulate business logic.

``LibraryUpdateService`` lives in ``gui/services/`` and acts as a Qt
presentation-layer coordinator.  It must:

- delegate all business decisions to application-layer use cases and services,
- never import from ``iPhoto.infrastructure`` directly,
- not define module-level loops or complex business-rule logic,
- not re-implement logic that already lives in application services.

Checked constraints
-------------------
1. The module must not import from ``iPhoto.infrastructure`` at any scope
   (infrastructure is accessed through application services only).
2. No ``for`` or ``while`` loops at module scope (business logic at import time).
3. The class must keep delegating to the application services that were
   extracted during Phase 3/4 (they must still be referenced in the source).
4. No inline file-I/O calls (``open(…)``) outside a ``TYPE_CHECKING`` guard –
   all I/O must go through use cases.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "iPhoto"
SERVICE_FILE = SRC_ROOT / "gui" / "services" / "library_update_service.py"

INFRA_PACKAGE = "iPhoto.infrastructure"

# Application-layer service names that LibraryUpdateService should still
# delegate to.  If any of these disappear from the source the test fails,
# indicating that business logic may have been pulled back into this file.
REQUIRED_DELEGATE_REFERENCES = {
    "MoveAftercareService",
    "RestoreAftercareService",
    "LibraryReloadService",
    "MergeTrashRestoreMetadataUseCase",
    "PersistScanResultUseCase",
}


def _parse() -> ast.Module:
    return ast.parse(SERVICE_FILE.read_text(encoding="utf-8"))


def _source() -> str:
    return SERVICE_FILE.read_text(encoding="utf-8")


def test_library_update_service_does_not_import_infrastructure() -> None:
    """LibraryUpdateService must not directly import from infrastructure."""
    tree = _parse()
    violations: list[int] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(INFRA_PACKAGE):
                violations.append(node.lineno)
            parts = module.lstrip(".").split(".")
            if parts and parts[0] == "infrastructure":
                violations.append(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(INFRA_PACKAGE):
                    violations.append(node.lineno)

    assert not violations, (
        f"library_update_service.py imports from infrastructure at lines: {violations}\n"
        "Route infrastructure calls through application services instead."
    )


def test_library_update_service_has_no_module_level_loops() -> None:
    """No for/while loops at module scope in library_update_service.py."""
    tree = _parse()
    violations: list[int] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.For, ast.While)):
            violations.append(node.lineno)
    assert not violations, (
        f"Module-level loops in library_update_service.py at lines: {violations}\n"
        "Business logic must live in application use cases, not in the Qt service."
    )


def test_library_update_service_still_delegates_to_application_services() -> None:
    """LibraryUpdateService must still reference the extracted application services.

    If these references disappear it is a signal that business logic may have
    been pulled back into this Qt coordinator rather than kept in the
    application layer.
    """
    source = _source()
    missing = [name for name in REQUIRED_DELEGATE_REFERENCES if name not in source]
    assert not missing, (
        f"library_update_service.py no longer references: {missing}\n"
        "These application services must remain as delegates. "
        "Do not re-implement their logic inside the Qt service."
    )


def test_library_update_service_has_no_inline_file_open() -> None:
    """LibraryUpdateService must not contain inline open() file I/O calls.

    All file access must go through application use cases.
    """
    tree = _parse()
    violations: list[int] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            violations.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == "open":
            violations.append(node.lineno)

    assert not violations, (
        f"Direct open() calls found in library_update_service.py at lines: {violations}\n"
        "All file I/O must be performed inside application use cases."
    )
