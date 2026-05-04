from __future__ import annotations

from pathlib import Path

from iPhoto.application.services.pinned_state_service import PinnedSidebarStateService


class _MemoryPinnedRepository:
    def __init__(self) -> None:
        self.payload: dict[str, list[dict[str, object]]] = {}
        self.save_count = 0

    def load_pinned_items_payload(self) -> dict[str, list[dict[str, object]]]:
        return {key: [dict(entry) for entry in entries] for key, entries in self.payload.items()}

    def save_pinned_items_payload(
        self,
        payload: dict[str, list[dict[str, object]]],
    ) -> None:
        self.payload = {key: [dict(entry) for entry in entries] for key, entries in payload.items()}
        self.save_count += 1


class _PeopleResolver:
    def __init__(
        self,
        *,
        clusters: set[str] | None = None,
        groups: set[str] | None = None,
    ) -> None:
        self.clusters = clusters or set()
        self.groups = groups or set()

    def has_cluster(self, person_id: str) -> bool:
        return person_id in self.clusters

    def has_group(self, group_id: str) -> bool:
        return group_id in self.groups


def test_pinned_state_service_scopes_items_by_library(tmp_path: Path) -> None:
    repository = _MemoryPinnedRepository()
    service = PinnedSidebarStateService(repository)
    library_a = tmp_path / "LibraryA"
    library_b = tmp_path / "LibraryB"
    library_a.mkdir()
    library_b.mkdir()

    assert service.pin_album(library_a / "Trips", "Trips", library_root=library_a)
    assert service.pin_person("person-a", "Alice", library_root=library_a)
    assert service.pin_group("group-a", "Group 1", library_root=library_b)

    assert [(item.kind, item.label) for item in service.items_for_library(library_a)] == [
        ("person", "Alice"),
        ("album", "Trips"),
    ]
    assert [(item.kind, item.label) for item in service.items_for_library(library_b)] == [
        ("group", "Group 1"),
    ]
    assert repository.save_count == 3


def test_pinned_state_service_remaps_child_album_and_preserves_custom_label(
    tmp_path: Path,
) -> None:
    repository = _MemoryPinnedRepository()
    service = PinnedSidebarStateService(repository)
    library_root = tmp_path / "Library"
    old_parent = library_root / "Trips"
    old_child = old_parent / "Paris"
    new_parent = library_root / "Renamed Trips"
    new_child = new_parent / "Paris"
    old_child.mkdir(parents=True)
    new_child.mkdir(parents=True)

    assert service.pin_album(old_child, "Paris", library_root=library_root)
    assert service.rename_item(
        kind="album",
        item_id=old_child,
        label="Favorite Paris",
        library_root=library_root,
    )
    assert service.remap_album_path(
        old_parent,
        new_parent,
        library_root=library_root,
        fallback_label="Renamed Trips",
    )

    items = service.items_for_library(library_root)
    assert len(items) == 1
    assert items[0].item_id == str(new_child.resolve())
    assert items[0].label == "Favorite Paris"
    assert items[0].custom_label is True


def test_pinned_state_service_redirects_and_prunes_people_entities(tmp_path: Path) -> None:
    repository = _MemoryPinnedRepository()
    resolver = _PeopleResolver(clusters={"person-new", "person-ok"}, groups={"group-ok"})
    service = PinnedSidebarStateService(
        repository,
        people_resolver_getter=lambda _library_root: resolver,
    )
    library_root = tmp_path / "Library"
    library_root.mkdir()
    service.pin_person("person-old", "Alice", library_root=library_root)
    service.pin_person("person-ok", "Bob", library_root=library_root)
    service.pin_group("group-old", "Old Group", library_root=library_root)
    service.pin_group("group-ok", "Current Group", library_root=library_root)

    assert service.prune_missing_people_entities(
        library_root,
        person_ids=("person-old", "person-ok"),
        group_ids=("group-old", "group-ok"),
        person_redirects={"person-old": "person-new"},
        group_redirects={"group-old": None},
    )

    items = {(item.kind, item.item_id) for item in service.items_for_library(library_root)}
    assert ("person", "person-old") not in items
    assert ("person", "person-new") in items
    assert ("person", "person-ok") in items
    assert ("group", "group-old") not in items
    assert ("group", "group-ok") in items
