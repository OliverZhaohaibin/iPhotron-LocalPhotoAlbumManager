"""Settings-backed storage for user-pinned sidebar entries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from iPhoto.settings.manager import SettingsManager

_GROUP_LABEL_RE = re.compile(r"^Group (\d+)$")


@dataclass(frozen=True, slots=True)
class PinnedSidebarItem:
    """Serializable representation of a pinned sidebar entry."""

    kind: str
    item_id: str
    label: str


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
            if kind not in {"album", "person", "group"} or not item_id or not label:
                continue
            resolved.append(PinnedSidebarItem(kind=kind, item_id=item_id, label=label))
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
            PinnedSidebarItem(kind="album", item_id=normalized_id, label=label.strip()),
            library_root=library_root,
        )

    def pin_person(self, person_id: str, label: str, *, library_root: Path | None) -> None:
        normalized_id = self._normalize_item_id("person", person_id)
        if normalized_id is None:
            return
        self._write_item(
            PinnedSidebarItem(kind="person", item_id=normalized_id, label=label.strip()),
            library_root=library_root,
        )

    def pin_group(self, group_id: str, label: str, *, library_root: Path | None) -> None:
        normalized_id = self._normalize_item_id("group", group_id)
        if normalized_id is None:
            return
        self._write_item(
            PinnedSidebarItem(kind="group", item_id=normalized_id, label=label.strip()),
            library_root=library_root,
        )

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

    def prune_missing_entity(self, *, kind: str, item_id: str, library_root: Path | None) -> None:
        self.unpin(kind=kind, item_id=item_id, library_root=library_root)

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
            },
        )
        payload[library_key] = filtered
        self._persist(payload)

    def _payload(self) -> dict[str, list[dict[str, str]]]:
        stored = self._settings.get(self._SETTINGS_KEY, {}) or {}
        if not isinstance(stored, dict):
            return {}
        payload: dict[str, list[dict[str, str]]] = {}
        for library_key, entries in stored.items():
            try:
                normalized_key = str(Path(str(library_key)).expanduser().resolve())
            except (OSError, ValueError):
                normalized_key = str(library_key)
            if not isinstance(entries, list):
                continue
            payload[normalized_key] = [entry for entry in entries if isinstance(entry, dict)]
        return payload

    def _persist(self, payload: dict[str, list[dict[str, str]]]) -> None:
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
