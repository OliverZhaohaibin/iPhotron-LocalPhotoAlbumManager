"""Lazy InfoPanel and location coordination."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from iPhoto.gui.coordinators.contracts import LocationInfoDetailPort


class LocationInfoCoordinator(QObject):
    """Create location/search resources only when the Info panel is opened."""

    def __init__(
        self,
        *,
        window,
        event_bus,
        detail: LocationInfoDetailPort,
        map_runtime_getter: Callable[[], object | None],
        package_root_resolver: Callable[[object | None], Path],
        map_extension_download,
        library_root_getter: Callable[[], Path | None],
        recognition_provider: Callable[[], object] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        from iPhoto.gui.services.location_file_write_queue import LocationFileWriteQueue
        from iPhoto.gui.services.location_search_controller import LocationSearchController
        from iPhoto.gui.ui.tasks.info_panel_metadata_worker import InfoPanelMetadataWorker
        from iPhoto.application.services.location_assignment_service import (
            LocationAssignmentService,
        )
        from iPhoto.infrastructure.repositories.location_assignment_repository import (
            IndexStoreLocationAssignmentRepository,
        )

        self._window = window
        self._detail = detail
        self._map_runtime_getter = map_runtime_getter
        self._package_root_resolver = package_root_resolver
        self._map_extension_download = map_extension_download
        self._library_root_getter = library_root_getter
        self._recognition_provider = recognition_provider
        self._recognition_initialized = False
        self._recognition_initialization_attempted = False
        self._panel = None
        self._write_queue = LocationFileWriteQueue(event_bus=event_bus, parent=self)
        detail.configure_location_domain(
            search_controller_factory=LocationSearchController,
            assignment_service_factory=LocationAssignmentService,
            assignment_repository_factory=IndexStoreLocationAssignmentRepository,
            metadata_worker_factory=InfoPanelMetadataWorker,
        )
        self._write_queue.bind_library_root(library_root_getter())
        detail.set_location_write_queue(self._write_queue)

    @property
    def write_queue(self):
        return self._write_queue

    def toggle(self) -> None:
        ui = self._window.ui
        panel = getattr(ui, "info_panel", None)
        if panel is not None and panel.isVisible():
            self._detail.toggle_info_panel()
            return
        if panel is None:
            panel = ui.ensure_info_panel()
        panel.set_map_runtime(self._map_runtime_getter())
        if self._panel is not panel:
            self._detail.set_info_panel(panel)
            panel.downloadMapExtensionRequested.connect(
                lambda: self._map_extension_download.start_download(source="info_panel")
            )
            self._panel = panel
        self._initialize_recognition_once(panel)
        self._detail.toggle_info_panel()

    def _initialize_recognition_once(self, panel) -> None:
        if self._recognition_initialization_attempted:
            return
        self._recognition_initialization_attempted = True
        if self._recognition_provider is not None:
            try:
                recognition = self._recognition_provider()
            except Exception:  # noqa: BLE001 - optional runtime boundary
                LOGGER.warning("Failed to initialise Info Panel recognition", exc_info=True)
                panel.set_face_actions_enabled(False)
                return
            self._recognition_initialized = recognition is not None
        if not self._recognition_initialized:
            panel.set_face_actions_enabled(False)

    def rebind_library(self) -> None:
        self._recognition_initialized = False
        self._recognition_initialization_attempted = False
        map_runtime = self._map_runtime_getter()
        self._write_queue.bind_library_root(self._library_root_getter())
        self._detail.set_map_runtime(map_runtime)
        self._map_extension_download.set_package_root(
            self._package_root_resolver(map_runtime)
        )
        panel = getattr(self._window.ui, "info_panel", None)
        if panel is not None:
            panel.set_map_runtime(map_runtime)

    def drain(self) -> None:
        self._write_queue.drain(timeout=None)

    def shutdown(self) -> None:
        self._write_queue.shutdown(wait=True)


__all__ = ["LocationInfoCoordinator"]
