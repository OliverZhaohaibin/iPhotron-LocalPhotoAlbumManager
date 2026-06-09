"""Find untranslated user-visible strings in GUI high-risk APIs."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

HIGH_RISK_METHODS = {
    "addAction",
    "addMenu",
    "setPlaceholderText",
    "setStatusTip",
    "setText",
    "setToolTip",
    "setWindowTitle",
    "showMessage",
}

HIGH_RISK_FUNCTIONS = {
    "QAction",
}

HIGH_RISK_QT_STATIC = {
    ("QMessageBox", "critical"),
    ("QMessageBox", "information"),
    ("QMessageBox", "question"),
    ("QMessageBox", "warning"),
    ("QInputDialog", "getText"),
    ("QFileDialog", "getExistingDirectory"),
    ("QFileDialog", "getOpenFileName"),
}

ALLOWED_LITERAL_TEXTS = {
    "",
    " ",
    "▶",
    "⏸",
    "Deutsch",
    "简体中文",
}

ALLOWED_DEMO_FILES = {
    "edit_strip.py",
    "edit_topbar.py",
}


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    api: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.api} uses untranslated string {self.text!r}"


def check(paths: Iterable[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for source_path in _python_files(paths):
        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        except SyntaxError as exc:
            violations.append(
                Violation(source_path, exc.lineno or 1, "parse", f"SyntaxError: {exc.msg}"),
            )
            continue
        visitor = _Visitor(source_path)
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations


def _python_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
    return sorted(files)


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[Violation] = []
        self._main_guard_depth = 0

    def visit_If(self, node: ast.If) -> None:
        if _is_main_guard(node.test):
            self._main_guard_depth += 1
            for child in node.body:
                self.visit(child)
            self._main_guard_depth -= 1
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        api = _high_risk_api(node.func)
        if api is not None and not self._is_allowed_context():
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    self._maybe_report(node, api, arg.value)
        self.generic_visit(node)

    def _is_allowed_context(self) -> bool:
        return self._main_guard_depth > 0 and self.path.name in ALLOWED_DEMO_FILES

    def _maybe_report(self, node: ast.AST, api: str, text: str) -> None:
        if not _looks_user_visible_english(text):
            return
        self.violations.append(Violation(self.path, getattr(node, "lineno", 1), api, text))


def _high_risk_api(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name) and func.id in HIGH_RISK_FUNCTIONS:
        return func.id
    if isinstance(func, ast.Attribute):
        if func.attr in HIGH_RISK_METHODS:
            return func.attr
        if isinstance(func.value, ast.Name) and (func.value.id, func.attr) in HIGH_RISK_QT_STATIC:
            return f"{func.value.id}.{func.attr}"
    return None


def _looks_user_visible_english(text: str) -> bool:
    if text in ALLOWED_LITERAL_TEXTS:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if not any("A" <= char <= "Z" or "a" <= char <= "z" for char in stripped):
        return False
    if not any("a" <= char <= "z" for char in stripped):
        return False
    return True


def _is_main_guard(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    left = test.left
    right = test.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)

    violations = check(args.paths)
    if violations:
        print("Untranslated GUI strings found:")
        for violation in violations:
            print(f"  {violation}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
