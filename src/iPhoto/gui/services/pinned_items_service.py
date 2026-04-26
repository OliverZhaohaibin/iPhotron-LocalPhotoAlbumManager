"""Settings-backed storage for user-pinned sidebar entries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from iPhoto.people.service import PeopleService
from iPhoto.settings.manager import SettingsManager

_GROUP_LABEL_RE = re.compile(r"^Group (\d+)$")


@dataclass(frozen=True, slots=True)
class PinnedSidebarItem:
    """Serializable representation of a pinned sidebar entry."""

    kind: str
    item_id: str
    label: str
    custom_label: bool = False


class PinnedItemsService(QObject):
    """Persist and publish user-managed pinned sidebar entries."""

    changed = Signal()

    _SETTINGS_KEY = "pinned_items_by_library"

    def __init__(self, settings: SettingsManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings

    def items_for_library(self, library_root: Path | None) -> list[PinnedSidebarItem]:
        library_key = self._library_key(library_root)
        if library_key is None:
            return []
        payload = self._settings.get(self._SETTINGS_KEY, {}) or {}
        entries = payload.get(library_key, [])
        if not isinstance(entries, list):
            return []
        resolved: list[PinnedSidebarItem] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            kind = str(entry.get("kind") or "").strip()
            item_id = str(entry.get("item_id") or "").strip()
            label = str(entry.get("label") or "").strip()
            custom_label = bool(entry.get("custom_label", False))
            if kind not in {"album", "person", "group"} or not item_id or not label:
                continue
            resolved.append(
                PinnedSidebarItem(
                    kind=kind,
                    item_id=item_id,
                    label=label,
                    custom_label=custom_label,
                )
            )
        return resolved

    def is_pinned(self, *, kind: str, item_id: str, library_root: Path | None) -> bool:
        normalized_id = self._normalize_item_id(kind, item_id)
        if normalized_id is None:
            return False
        return any(
            item.kind == kind and item.item_id == normalized_id
            for item in self.items_for_library(library_root)
        )

    def pin_album(self, album_path: Path, label: str, *, library_root: Path | None) -> None:
        normalized_id = self._normalize_item_id("album", album_path)
        if normalized_id is None:
            return
        self._write_item(
            PinnedSidebarItem(
                kind="album",
                item_id=normalized_id,
                label=label.strip(),
                custom_label=False,
            ),
            library_root=library_root,
        )

    def pin_person(self, person_id: str, label: str, *, library_root: Path | None) -> None:
        normalized_id = self._normalize_item_id("person", person_id)
        if normalized_id is None:
            return
        self._write_item(
            PinnedSidebarItem(
                kind="person",
                item_id=normalized_id,
                label=label.strip(),
                custom_label=False,
            ),
            library_root=library_root,
        )

    def pin_group(self, group_id: str, label: str, *, library_root: Path | None) -> None:
        normalized_id = self._normalize_item_id("group", group_id)
        if normalized_id is None:
            return
        self._write_item(
            PinnedSidebarItem(
                kind="group",
                item_id=normalized_id,
                label=label.strip(),
                custom_label=False,
            ),
            library_root=library_root,
        )

    def rename_item(
        self,
        *,
        kind: str,
        item_id: str | Path,
        label: str,
        library_root: Path | None,
    ) -> bool:
        normalized_id = self._normalize_item_id(kind, item_id)
        library_key = self._library_key(library_root)
        target_label = str(label or "").strip()
        if normalized_id is None or library_key is None or not target_label:
            return False

        payload = self._payload()
        entries = payload.get(library_key, [])
        updated = False
        rewritten: list[dict[str, object]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_kind = str(entry.get("kind") or "").strip()
            entry_item_id = str(entry.get("item_id") or "").strip()
            if entry_kind == kind and entry_item_id == normalized_id:
                next_entry = dict(entry)
                next_entry["label"] = target_label
                next_entry["custom_label"] = True
                rewritten.append(next_entry)
                updated = True
                continue
            rewritten.append(dict(entry))

        if not updated:
            return False

        payload[library_key] = rewritten
        self._persist(payload)
        return True

    def unpin(self, *, kind: str, item_id: str, library_root: Path | None) -> None:
        normalized_id = self._normalize_item_id(kind, item_id)
        library_key = self._library_key(library_root)
        if normalized_id is None or library_key is None:
            return
        payload = self._payload()
        entries = payload.get(library_key, [])
        filtered = [
            entry
            for entry in entries
            if not (
                isinstance(entry, dict)
                and str(entry.get("kind") or "").strip() == kind
                and str(entry.get("item_id") or "").strip() == normalized_id
            )
        ]
        if len(filtered) == len(entries):
            return
        payload[library_key] = filtered
        self._persist(payload)

    def next_group_label(self, library_root: Path | None) -> str:
        next_index = 1
        for item in self.items_for_library(library_root):
            if item.kind != "group":
                continue
            match = _GROUP_LABEL_RE.match(item.label.strip())
            if match is None:
                continue
            next_index = max(next_index, int(match.group(1)) + 1)
        return f"Group {next_index}"

    def prune_missing_album(self, album_path: Path, *, library_root: Path | None) -> None:
        self.unpin(kind="album", item_id=str(album_path), library_root=library_root)

    def remap_album_path(
        self,
        old_path: Path,
        new_path: Path,
        *,
        library_root: Path | None,
        fallback_label: str | None = None,
    ) -> bool:
        old_id = self._normalize_item_id("album", old_path)
        new_id = self._normalize_item_id("album", new_path)
        library_key = self._library_key(library_root)
        if old_id is None or new_id is None or library_key is None:
            return False

        payload = self._payload()
        entries = payload.get(library_key, [])
        rewritten: list[dict[str, object]] = []
        updated = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_kind = str(entry.get("kind") or "").strip()
            entry_item_id = str(entry.get("item_id") or "").strip()
            next_entry = dict(entry)
            remapped_id = (
                self._remap_album_item_id(entry_item_id, old_id, new_id)
                if entry_kind == "album"
                else None
            )
            if remapped_id is not None:
                next_entry["item_id"] = remapped_id
                if remapped_id == new_id and not bool(next_entry.get("custom_label", False)):
                    label = str(fallback_label or new_path.name).strip()
                    if label:
                        next_entry["label"] = label
                updated = True
            rewritten.append(next_entry)

        if not updated:
            return False

        rewritten = self._dedupe_entries(rewritten)

        payload[library_key] = rewritten
        self._persist(payload)
        return True

    def _remap_album_item_id(
        self,
        item_id: str,
        old_id: str,
        new_id: str,
    ) -> str | None:
        if item_id == old_id:
            return new_id
        try:
            rel = Path(item_id).resolve().relative_to(Path(old_id).resolve())
        except (OSError, ValueError):
            try:
                rel = Path(item_id).relative_to(Path(old_id))
            except ValueError:
                return None
        if rel.as_posix() in ("", "."):
            return new_id
        try:
            return str((Path(new_id) / rel).resolve())
        except OSError:
            return str(Path(new_id) / rel)

    @staticmethod
    def _dedupe_entries(entries: list[dict[str, object]]) -> list[dict[str, object]]:
        deduped: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            kind = str(entry.get("kind") or "").strip()
            item_id = str(entry.get("item_id") or "").strip()
            if kind and item_id:
                key = (kind, item_id)
                if key in seen:
                    continue
                seen.add(key)
            deduped.append(entry)
        return deduped

    def prune_missing_entity(self, *, kind: str, item_id: str, library_root: Path | None) -> None:
        self.unpin(kind=kind, item_id=item_id, library_root=library_root)

    def prune_missing_people_entities(
        self,
        library_root: Path | None,
        *,
        person_ids: tuple[str, ...] = (),
        group_ids: tuple[str, ...] = (),
        person_redirects: dict[str, str] | None = None,
        group_redirects: dict[str, str | None] | None = None,
    ) -> bool:
        library_key = self._library_key(library_root)
        if library_key is None:
            return False

        person_redirect_map = {
            str(source).strip(): str(target).strip()
            for source, target in (person_redirects or {}).items()
            if str(source).strip() and str(target).strip()
        }
        group_redirect_map = {
            str(source).strip(): (str(target).strip() if target is not None else None)
            for source, target in (group_redirects or {}).items()
            if str(source).strip()
        }
        target_person_ids = tuple(dict.fromkeys(person_id for person_id in person_ids if person_id))
        target_group_ids = tuple(dict.fromkeys(group_id for group_id in group_ids if group_id))
        if (
            not target_person_ids
            and not target_group_ids
            and not person_redirect_map
            and not group_redirect_map
        ):
            return False

        payload = self._payload()
        entries = payload.get(library_key, [])
        rewritten: list[dict[str, object]] = []
        updated = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_kind = str(entry.get("kind") or "").strip()
            entry_item_id = str(entry.get("item_id") or "").strip()
            next_entry = dict(entry)
            if entry_kind == "person" and entry_item_id in person_redirect_map:
                next_entry["item_id"] = person_redirect_map[entry_item_id]
                updated = True
            elif entry_kind == "group" and entry_item_id in group_redirect_map:
                redirect_target = group_redirect_map[entry_item_id]
                if redirect_target is None:
                    updated = True
                    continue
                next_entry["item_id"] = redirect_target
                updated = True
            rewritten.append(next_entry)

        if updated:
            entries = self._dedupe_entries(rewritten)
            payload[library_key] = entries

        pinned_items = [
            PinnedSidebarItem(
                kind=str(entry.get("kind") or "").strip(),
                item_id=str(entry.get("item_id") or "").strip(),
                label=str(entry.get("label") or "").strip(),
                custom_label=bool(entry.get("custom_label", False)),
            )
            for entry in entries
            if isinstance(entry, dict)
            and str(entry.get("kind") or "").strip() in {"person", "group"}
            and str(entry.get("item_id") or "").strip()
            and str(entry.get("label") or "").strip()
        ]
        pinned_person_ids = {
            item.item_id
            for item in pinned_items
            if item.kind == "person" and item.item_id in target_person_ids
        }
        pinned_group_ids = {
            item.item_id
            for item in pinned_items
            if item.kind == "group" and item.item_id in target_group_ids
        }
        if not pinned_person_ids and not pinned_group_ids:
            if updated:
                self._persist(payload)
                return True
            return False

        people_service = PeopleService(Path(library_key))
        stale_person_ids = [
            person_id for person_id in pinned_person_ids if not people_service.has_cluster(person_id)
        ]
        stale_group_ids = [
            group_id for group_id in pinned_group_ids if not people_service.has_group(group_id)
        ]
        if not stale_person_ids and not stale_group_ids:
            return False

        filtered = [
            entry
            for entry in entries
            if not (
                isinstance(entry, dict)
                and (
                    (
                        str(entry.get("kind") or "").strip() == "person"
                        and str(entry.get("item_id") or "").strip() in stale_person_ids
                    )
                    or (
                        str(entry.get("kind") or "").strip() == "group"
                        and str(entry.get("item_id") or "").strip() in stale_group_ids
                    )
                )
            )
        ]
        if len(filtered) == len(entries):
            if updated:
                self._persist(payload)
                return True
            return False
        payload[library_key] = filtered
        self._persist(payload)
        return True

    def _write_item(self, item: PinnedSidebarItem, *, library_root: Path | None) -> None:
        if not item.label:
            return
        library_key = self._library_key(library_root)
        if library_key is None:
            return
        payload = self._payload()
        entries = payload.get(library_key, [])
        filtered = [
            entry
            for entry in entries
            if not (
                isinstance(entry, dict)
                and str(entry.get("kind") or "").strip() == item.kind
                and str(entry.get("item_id") or "").strip() == item.item_id
            )
        ]
        filtered.insert(
            0,
            {
                "kind": item.kind,
                "item_id": item.item_id,
                "label": item.label,
                "custom_label": item.custom_label,
            },
        )
        payload[library_key] = filtered
        self._persist(payload)

    def _payload(self) -> dict[str, list[dict[str, object]]]:
        stored = self._settings.get(self._SETTINGS_KEY, {}) or {}
        if not isinstance(stored, dict):
            return {}
        payload: dict[str, list[dict[str, object]]] = {}
        for library_key, entries in stored.items():
            try:
                normalized_key = str(Path(str(library_key)).expanduser().resolve())
            except (OSError, ValueError):
                normalized_key = str(library_key)
            if not isinstance(entries, list):
                continue
            payload[normalized_key] = [entry for entry in entries if isinstance(entry, dict)]
        return payload

    def _persist(self, payload: dict[str, list[dict[str, object]]]) -> None:
        self._settings.set(self._SETTINGS_KEY, payload)
        self.changed.emit()

    def _library_key(self, library_root: Path | None) -> str | None:
        if library_root is None:
            return None
        try:
            return str(library_root.expanduser().resolve())
        except OSError:
            return str(library_root)

    def _normalize_item_id(self, kind: str, item_id: str | Path) -> str | None:
        if kind == "album":
            try:
                return str(Path(item_id).expanduser().resolve())
            except (OSError, TypeError, ValueError):
                return None
        normalized = str(item_id or "").strip()
        return normalized or None


__all__ = ["PinnedItemsService", "PinnedSidebarItem"]
