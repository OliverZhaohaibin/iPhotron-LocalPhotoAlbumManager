"""Architecture test: adapter layer must not directly import infrastructure.

Adapters in ``presentation/qt/adapters/`` and ``gui/services/`` act as the
boundary between the application layer and the Qt presentation layer.  They
must communicate with infrastructure *only* through application services,
never by importing from ``iPhoto.infrastructure`` directly.

Violation: any ``from iPhoto.infrastructure…`` or ``import iPhoto.infrastructure…``
at the top level of an adapter module (including outside TYPE_CHECKING blocks).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "iPhoto"

ADAPTER_DIRS = [
    SRC_ROOT / "presentation" / "qt" / "adapters",
    SRC_ROOT / "gui" / "services",
]

INFRA_PACKAGE = "iPhoto.infrastructure"
INFRA_RELATIVE_MARKER = "infrastructure"


def _collect_py_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.rglob("*.py"))


def _direct_infra_imports(source: str) -> list[int]:
    """Return line numbers where the file directly imports from infrastructure."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    violations: list[int] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_infra_module(module):
                violations.append(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(INFRA_PACKAGE):
                    violations.append(node.lineno)

    return violations


def _is_infra_module(module: str) -> bool:
    """Return True when *module* refers to the infrastructure package."""
    if module.startswith(INFRA_PACKAGE):
        return True
    parts = module.lstrip(".").split(".")
    if parts and parts[0] == INFRA_RELATIVE_MARKER:
        return True
    return False


def test_adapter_modules_do_not_import_infrastructure() -> None:
    """Adapter modules must not directly import from infrastructure."""
    violations: list[str] = []

    for directory in ADAPTER_DIRS:
        for py_file in _collect_py_files(directory):
            if py_file.name == "__init__.py":
                continue
            source = py_file.read_text(encoding="utf-8")
            lines = _direct_infra_imports(source)
            for lineno in lines:
                rel = py_file.relative_to(SRC_ROOT.parent.parent)
                violations.append(f"{rel}:{lineno}")

    assert not violations, (
        "Adapter modules directly import from infrastructure (violates boundary):\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nFix: route infrastructure calls through application services instead."
    )
