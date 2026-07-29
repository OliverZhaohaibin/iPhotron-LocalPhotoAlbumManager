# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path


def test_legacy_application_tree_is_not_restored() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    legacy_root = repository_root / "src" / "iPhoto" / "legacy"

    assert not list(legacy_root.rglob("*.py"))
