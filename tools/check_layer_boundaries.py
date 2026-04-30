#!/usr/bin/env python3
"""Check vNext layer import boundaries."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

LOWER_LAYER_GUI_FORBIDDEN_ROOTS = {
    "cache",
    "core",
    "infrastructure",
    "io",
    "library",
    "people",
}

LEGACY_MODEL_IMPORT_EXCEPTIONS = {
    "app.py",
    "cli.py",
    "core/pairing.py",
    "gui/facade.py",
    "gui/services/album_metadata_service.py",
    "gui/services/asset_move_service.py",
    "gui/ui/tasks/incremental_refresh_worker.py",
    "gui/ui/widgets/albums_dashboard.py",
    "index_sync_service.py",
    "library/album_operations.py",
}

PEOPLE_INDEX_STORE_FORBIDDEN_FILES = {
    "library/workers/face_scan_worker.py",
}

INDEX_SYNC_FORBIDDEN_FILES = {
    "index_sync_service.py",
}

ASSET_RUNTIME_SQLITE_FORBIDDEN_FILES = {
    "infrastructure/services/library_asset_runtime.py",
}

ASSET_RUNTIME_SQLITE_FORBIDDEN_IMPORTS = {
    "iPhoto.infrastructure.db.pool",
    "iPhoto.infrastructure.repositories.sqlite_asset_repository",
}

LEGACY_DOMAIN_USE_CASE_MODULES = {
    "iPhoto.application.use_cases.aggregate_geo_data",
    "iPhoto.application.use_cases.apply_edit",
    "iPhoto.application.use_cases.export_assets",
    "iPhoto.application.use_cases.generate_thumbnail",
    "iPhoto.application.use_cases.import_assets",
    "iPhoto.application.use_cases.manage_trash",
    "iPhoto.application.use_cases.move_assets",
    "iPhoto.application.use_cases.open_album",
    "iPhoto.application.use_cases.pair_live_photos",
    "iPhoto.application.use_cases.scan_album",
    "iPhoto.application.use_cases.update_metadata",
}

LEGACY_DOMAIN_USE_CASE_ALLOWED_IMPORTERS = {
    "application/services/album_service.py",
    "application/services/asset_service.py",
    "application/use_cases/__init__.py",
    "bootstrap/container.py",
    "io/scanner_adapter.py",
}

LEGACY_DOMAIN_USE_CASE_PACKAGE = "iPhoto.application.use_cases"


def _is_type_checking_guard(node: ast.If) -> bool:
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


def _module_for_file(py_file: Path, src_root: Path) -> str:
    rel = py_file.relative_to(src_root).with_suffix("")
    parts = ["iPhoto", *rel.parts]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_import_from(
    *,
    current_module: str,
    is_package: bool,
    level: int,
    module: str | None,
) -> str:
    if level == 0:
        return module or ""

    package_parts = current_module.split(".") if is_package else current_module.split(".")[:-1]
    base_len = max(0, len(package_parts) - level + 1)
    parts = package_parts[:base_len]
    if module:
        parts.extend(module.split("."))
    return ".".join(parts)


def _is_or_under(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


class _ImportCollector(ast.NodeVisitor):
    def __init__(self, current_module: str, is_package: bool) -> None:
        self.current_module = current_module
        self.is_package = is_package
        self.imports: list[tuple[int, str]] = []
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
            self.imports.append((node.lineno, alias.name))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._in_type_checking:
            return
        resolved = _resolve_import_from(
            current_module=self.current_module,
            is_package=self.is_package,
            level=node.level,
            module=node.module,
        )
        self.imports.append((node.lineno, resolved))
        if resolved == "iPhoto":
            for alias in node.names:
                self.imports.append((node.lineno, f"iPhoto.{alias.name}"))


def _runtime_imports(py_file: Path, src_root: Path) -> list[tuple[int, str]]:
    source = py_file.read_text(encoding="utf-8")
    tree = ast.parse(source)
    collector = _ImportCollector(
        _module_for_file(py_file, src_root),
        py_file.name == "__init__.py",
    )
    collector.visit(tree)
    return collector.imports


def _relative_key(py_file: Path, src_root: Path) -> str:
    return py_file.relative_to(src_root).as_posix()


def check(src_root: Path) -> list[str]:
    violations: list[str] = []
    for py_file in sorted(src_root.rglob("*.py")):
        rel = _relative_key(py_file, src_root)
        top_level = rel.split("/", 1)[0]
        try:
            imports = _runtime_imports(py_file, src_root)
        except SyntaxError as exc:
            violations.append(f"{py_file}: PARSE_ERROR - {exc}")
            continue

        for lineno, module in imports:
            if top_level == "application" and any(
                _is_or_under(module, forbidden)
                for forbidden in (
                    "iPhoto.gui",
                    "iPhoto.cache",
                    "iPhoto.infrastructure",
                )
            ):
                violations.append(
                    f"{py_file}:{lineno}: application imports concrete layer {module}"
                )

            if top_level in LOWER_LAYER_GUI_FORBIDDEN_ROOTS and _is_or_under(
                module,
                "iPhoto.gui",
            ):
                violations.append(
                    f"{py_file}:{lineno}: lower layer imports GUI module {module}"
                )

            if top_level == "gui" and (
                module == "iPhoto.cache"
                or _is_or_under(module, "iPhoto.cache.index_store")
            ):
                violations.append(
                    f"{py_file}:{lineno}: GUI imports concrete index store {module}"
                )

            if (
                (top_level == "people" or rel in PEOPLE_INDEX_STORE_FORBIDDEN_FILES)
                and (
                    module == "iPhoto.cache"
                    or _is_or_under(module, "iPhoto.cache.index_store")
                )
            ):
                violations.append(
                    f"{py_file}:{lineno}: People runtime imports concrete index store {module}"
                )

            if rel in INDEX_SYNC_FORBIDDEN_FILES and (
                module == "iPhoto.cache"
                or _is_or_under(module, "iPhoto.cache.index_store")
            ):
                violations.append(
                    f"{py_file}:{lineno}: index sync imports concrete index store {module}"
                )

            if rel in ASSET_RUNTIME_SQLITE_FORBIDDEN_FILES and any(
                _is_or_under(module, forbidden)
                for forbidden in ASSET_RUNTIME_SQLITE_FORBIDDEN_IMPORTS
            ):
                violations.append(
                    f"{py_file}:{lineno}: asset runtime imports retired SQLite repository path {module}"
                )

            if (
                rel not in LEGACY_MODEL_IMPORT_EXCEPTIONS
                and not rel.startswith("models/")
                and _is_or_under(module, "iPhoto.models")
            ):
                violations.append(
                    f"{py_file}:{lineno}: runtime imports legacy model shim {module}"
                )

            if rel not in LEGACY_DOMAIN_USE_CASE_ALLOWED_IMPORTERS and (
                module == LEGACY_DOMAIN_USE_CASE_PACKAGE
                or any(
                    _is_or_under(module, legacy_module)
                    for legacy_module in LEGACY_DOMAIN_USE_CASE_MODULES
                )
            ):
                violations.append(
                    f"{py_file}:{lineno}: runtime imports legacy domain-repository use case {module}"
                )

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
        print("Layer boundary violations found:\n")
        for violation in found:
            print(f"  {violation}")
        return 1

    print("OK - vNext layer boundaries are respected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
