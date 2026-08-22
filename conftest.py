"""Repository-wide pytest compatibility helpers."""

from __future__ import annotations

from collections.abc import Generator
from types import ModuleType

import pytest
from _pytest.monkeypatch import MonkeyPatch, notset


def _same_legacy_export(left: object, right: object) -> bool:
    if left is right:
        return True
    if isinstance(left, (str, int, float, bool, type(None))) and isinstance(
        right,
        (str, int, float, bool, type(None)),
    ):
        return left == right
    return False


class _PetsFacadeAwareMonkeyPatch(MonkeyPatch):
    """Keep legacy Pets patch points effective after the facade split.

    ``iPhoto.pets.pipeline`` intentionally remains the stable compatibility
    import path, while many legacy functions keep ``_pipeline_impl`` as their
    globals owner. Tests that replace a re-exported dependency on the facade
    therefore need the same replacement on the implementation module. Mirror
    only attributes that were genuine re-exports before the patch; facade-owned
    hardening classes/functions remain isolated. ``_download_file`` is the one
    deliberate exception because the facade wraps it while legacy detector
    acquisition still resolves the implementation global.
    """

    def setattr(self, target, name, value=notset, raising: bool = True) -> None:
        mirror_target: ModuleType | None = None
        attribute_name: str | None = None

        if value is notset:
            super().setattr(target, name, value, raising=raising)
            return

        if isinstance(target, ModuleType) and target.__name__ == "iPhoto.pets.pipeline":
            attribute_name = str(name)
            implementation = getattr(target, "_impl", None)
            if isinstance(implementation, ModuleType) and hasattr(implementation, attribute_name):
                facade_before = getattr(target, attribute_name, notset)
                impl_before = getattr(implementation, attribute_name, notset)
                if attribute_name == "_download_file" or _same_legacy_export(
                    facade_before,
                    impl_before,
                ):
                    mirror_target = implementation

        super().setattr(target, name, value, raising=raising)
        if mirror_target is not None and attribute_name is not None:
            super().setattr(mirror_target, attribute_name, value, raising=False)


@pytest.fixture
def monkeypatch() -> Generator[MonkeyPatch, None, None]:
    patcher = _PetsFacadeAwareMonkeyPatch()
    try:
        yield patcher
    finally:
        patcher.undo()
