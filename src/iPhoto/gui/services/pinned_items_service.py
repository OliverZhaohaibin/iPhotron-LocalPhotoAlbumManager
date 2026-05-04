"""Qt transport wrapper for user-pinned sidebar entries."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from iPhoto.application.services.pinned_state_service import (
    PinnedSidebarItem,
    PinnedSidebarStateService,
)
from iPhoto.people.service import PeopleService
from iPhoto.settings.manager import SettingsManager


class _SettingsPinnedStateRepository:
    _SETTINGS_KEY = "pinned_items_by_library"

    def __init__(self, settings: SettingsManager) -> None:
        self._settings = settings

    def load_pinned_items_payload(self) -> dict[str, list[dict[str, object]]]:
        stored = self._settings.get(self._SETTINGS_KEY, {}) or {}
        if not isinstance(stored, dict):
            return {}
        payload: dict[str, list[dict[str, object]]] = {}
        for library_key, entries in stored.items():
            if not isinstance(entries, list):
                continue
            payload[str(library_key)] = [entry for entry in entries if isinstance(entry, dict)]
        return payload

    def save_pinned_items_payload(
        self,
        payload: dict[str, list[dict[str, object]]],
    ) -> None:
        self._settings.set(self._SETTINGS_KEY, payload)


class PinnedItemsService(QObject):
    """Persist and publish user-managed pinned sidebar entries."""

    changed = Signal()

    def __init__(
        self,
        settings: SettingsManager,
        people_service_getter: Callable[[Path | None], PeopleService | None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._people_service_getter = people_service_getter
        self._state = PinnedSidebarStateService(
            _SettingsPinnedStateRepository(settings),
            people_resolver_getter=self._people_service_for_library,
        )

    def items_for_library(self, library_root: Path | None) -> list[PinnedSidebarItem]:
        return self._state.items_for_library(library_root)

    def is_pinned(self, *, kind: str, item_id: str, library_root: Path | None) -> bool:
        return self._state.is_pinned(kind=kind, item_id=item_id, library_root=library_root)

    def pin_album(self, album_path: Path, label: str, *, library_root: Path | None) -> None:
        self._emit_if_changed(
            self._state.pin_album(album_path, label, library_root=library_root)
        )

    def pin_person(self, person_id: str, label: str, *, library_root: Path | None) -> None:
        self._emit_if_changed(
            self._state.pin_person(person_id, label, library_root=library_root)
        )

    def pin_group(self, group_id: str, label: str, *, library_root: Path | None) -> None:
        self._emit_if_changed(
            self._state.pin_group(group_id, label, library_root=library_root)
        )

    def rename_item(
        self,
        *,
        kind: str,
        item_id: str | Path,
        label: str,
        library_root: Path | None,
    ) -> bool:
        changed = self._state.rename_item(
            kind=kind,
            item_id=item_id,
            label=label,
            library_root=library_root,
        )
        self._emit_if_changed(changed)
        return changed

    def unpin(self, *, kind: str, item_id: str, library_root: Path | None) -> None:
        self._emit_if_changed(
            self._state.unpin(kind=kind, item_id=item_id, library_root=library_root)
        )

    def next_group_label(self, library_root: Path | None) -> str:
        return self._state.next_group_label(library_root)

    def prune_missing_album(self, album_path: Path, *, library_root: Path | None) -> None:
        self._emit_if_changed(
            self._state.prune_missing_album(album_path, library_root=library_root)
        )

    def remap_album_path(
        self,
        old_path: Path,
        new_path: Path,
        *,
        library_root: Path | None,
        fallback_label: str | None = None,
    ) -> bool:
        changed = self._state.remap_album_path(
            old_path,
            new_path,
            library_root=library_root,
            fallback_label=fallback_label,
        )
        self._emit_if_changed(changed)
        return changed

    def prune_missing_entity(self, *, kind: str, item_id: str, library_root: Path | None) -> None:
        self._emit_if_changed(
            self._state.prune_missing_entity(
                kind=kind,
                item_id=item_id,
                library_root=library_root,
            )
        )

    def prune_missing_people_entities(
        self,
        library_root: Path | None,
        *,
        person_ids: tuple[str, ...] = (),
        group_ids: tuple[str, ...] = (),
        person_redirects: dict[str, str] | None = None,
        group_redirects: dict[str, str | None] | None = None,
    ) -> bool:
        changed = self._state.prune_missing_people_entities(
            library_root,
            person_ids=person_ids,
            group_ids=group_ids,
            person_redirects=person_redirects,
            group_redirects=group_redirects,
        )
        self._emit_if_changed(changed)
        return changed

    def _people_service_for_library(self, library_root: Path) -> PeopleService | None:
        if self._people_service_getter is not None:
            service = self._people_service_getter(library_root)
            if service is not None:
                return service
        return PeopleService(Path(library_root))

    def _emit_if_changed(self, changed: bool) -> None:
        if changed:
            self.changed.emit()


__all__ = ["PinnedItemsService", "PinnedSidebarItem"]
