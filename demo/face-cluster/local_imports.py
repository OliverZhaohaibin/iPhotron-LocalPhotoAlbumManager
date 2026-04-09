from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


_MODULE_DIR = Path(__file__).resolve().parent
_MODULE_NAMESPACE = "face_cluster_demo"


def import_sibling(module_name: str) -> ModuleType:
    """Import a sibling module by file path to avoid demo-name collisions."""

    module_path = (_MODULE_DIR / f"{module_name}.py").resolve()
    existing = sys.modules.get(module_name)
    if existing is not None:
        existing_file = getattr(existing, "__file__", None)
        if existing_file and Path(existing_file).resolve() == module_path:
            return existing

    qualified_name = f"{_MODULE_NAMESPACE}.{module_name}"
    cached = sys.modules.get(qualified_name)
    if cached is not None:
        sys.modules[module_name] = cached
        return cached

    spec = importlib.util.spec_from_file_location(qualified_name, module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive guard
        raise ImportError(f"Unable to load sibling module '{module_name}' from {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


__all__ = ["import_sibling"]
