"""Helpers for resolving the active GUI People service."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from iPhoto.people.service import PeopleService

if TYPE_CHECKING:  # pragma: no cover
    from ...library.manager import LibraryManager


def resolve_people_service(
    library_manager: "LibraryManager | None",
    *,
    library_root: Path | None = None,
    allow_root_fallback: bool = False,
    default_to_unbound: bool = False,
) -> PeopleService | None:
    """Return the bound People service for *library_root* when available."""

    service = bound_people_service(library_manager, library_root=library_root)
    if service is not None:
        return service
    if allow_root_fallback and library_root is not None:
        return PeopleService(Path(library_root))
    if default_to_unbound:
        return PeopleService()
    return None


def bound_people_service(
    library_manager: "LibraryManager | None",
    *,
    library_root: Path | None = None,
) -> PeopleService | None:
    """Return the currently bound People service if it matches *library_root*."""

    if library_manager is None:
        return None

    candidate = getattr(library_manager, "people_service", None)
    if candidate is None or _is_unconfigured_mock(candidate):
        return None

    library_root_getter = getattr(candidate, "library_root", None)
    if not callable(library_root_getter):
        return None

    if library_root is None:
        return candidate

    bound_root = library_root_getter()
    if bound_root is None:
        return None

    return candidate if _paths_equal(Path(bound_root), Path(library_root)) else None


def _is_unconfigured_mock(candidate: object) -> bool:
    return candidate.__class__.__module__.startswith("unittest.mock")


def _paths_equal(left: Path, right: Path) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return Path(left) == Path(right)


__all__ = [
    "bound_people_service",
    "resolve_people_service",
]
