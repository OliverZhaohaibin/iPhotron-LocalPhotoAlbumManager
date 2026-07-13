"""Lazy People/Pets coordination for Detail and dashboard features."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from iPhoto.gui.coordinators.contracts import RecognitionDetailPort


class RecognitionCoordinator(QObject):
    """Bind recognition services only after a recognition surface is used."""

    def __init__(
        self,
        *,
        context,
        detail: RecognitionDetailPort,
        pinned_items_service,
        library_root_getter: Callable[[], Path | None],
        people_service_getter: Callable[..., object | None],
        pet_service_getter: Callable[..., object | None],
        cluster_callback: Callable[[str], None],
        group_callback: Callable[[str], None],
        pet_callback: Callable[[str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._detail = detail
        self._pinned_items_service = pinned_items_service
        self._library_root_getter = library_root_getter
        self._people_service_getter = people_service_getter
        self._pet_service_getter = pet_service_getter
        self._cluster_callback = cluster_callback
        self._group_callback = group_callback
        self._pet_callback = pet_callback
        self._people_page = None
        from iPhoto.gui.ui.tasks.manual_face_add_worker import ManualFaceAddWorker

        self._detail.configure_recognition_domain(
            manual_face_worker_factory=ManualFaceAddWorker,
        )
        self.rebind_library()

    def rebind_library(self) -> None:
        root = self._library_root_getter()
        people_service = self._people_service_getter(library_root=root)
        pet_service = self._pet_service_getter(library_root=root)
        self._detail.set_people_service(people_service)
        self._detail.set_pet_service(pet_service)
        self._detail.set_people_library_root(root)
        if self._people_page is not None:
            self._bind_services(self._people_page, root, people_service, pet_service)

    def set_face_name_display_enabled(self, enabled: bool) -> None:
        self._detail.set_face_name_display_enabled(enabled)

    def handle_snapshot_committed(self, event: object) -> None:
        self._detail.handle_people_snapshot_committed(event)

    def bind_people_page(self, people_page: object) -> None:
        if self._people_page is people_page:
            return
        self._people_page = people_page
        root = self._library_root_getter()
        self._apply_people_page_preferences(people_page)
        self._bind_services(
            people_page,
            root,
            self._people_service_getter(library_root=root),
            self._pet_service_getter(library_root=root),
        )
        people_page.clusterActivated.connect(self._cluster_callback)
        people_page.groupActivated.connect(self._group_callback)
        if hasattr(people_page, "petActivated"):
            people_page.petActivated.connect(self._pet_callback)
        library = self._context.library
        library.peopleIndexUpdated.connect(people_page.schedule_index_refresh)
        library.petIndexUpdated.connect(people_page.schedule_index_refresh)
        library.faceScanStatusChanged.connect(people_page.set_status_message)
        if hasattr(people_page, "set_pet_status_message"):
            library.petScanStatusChanged.connect(people_page.set_pet_status_message)

    def _apply_people_page_preferences(self, people_page: object) -> None:
        setter = getattr(people_page, "set_show_hidden_people", None)
        if not callable(setter):
            return
        stored = self._context.settings.get("ui.show_hidden_people", False)
        if isinstance(stored, str):
            enabled = stored.strip().lower() in {"1", "true", "yes", "on"}
        else:
            enabled = bool(stored)
        setter(enabled)

    def _bind_services(
        self,
        people_page: object,
        root: Path | None,
        people_service: object | None,
        pet_service: object | None,
    ) -> None:
        if people_service is not None and hasattr(people_page, "set_people_service"):
            people_page.set_people_service(people_service)
        elif hasattr(people_page, "set_library_root"):
            people_page.set_library_root(root)
        if hasattr(people_page, "set_pet_service"):
            people_page.set_pet_service(pet_service)
        if hasattr(people_page, "set_pinned_service"):
            people_page.set_pinned_service(self._pinned_items_service)
        people_page.set_status_message(self._context.library.face_scan_status_message())
        if hasattr(people_page, "set_pet_status_message"):
            people_page.set_pet_status_message(
                self._context.library.pet_scan_status_message()
            )


__all__ = ["RecognitionCoordinator"]
