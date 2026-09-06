"""Lazy People/Pets coordination for Detail and dashboard features."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, QTimer, Signal

if TYPE_CHECKING:
    from iPhoto.gui.coordinators.contracts import RecognitionDetailPort


@dataclass(frozen=True, slots=True)
class _DashboardSnapshot:
    root: Path
    generation: int
    index_version: int
    include_hidden: bool
    summaries: tuple[object, ...]
    groups: tuple[object, ...]
    pet_summaries: tuple[object, ...]
    pending: int
    pet_pending: int
    status_message: str | None
    pet_status_message: str | None


class _DashboardWarmupSignals(QObject):
    ready = Signal(object)
    failed = Signal(int, object, object)


class _DashboardWarmupWorker(QRunnable):
    def __init__(
        self,
        *,
        root: Path,
        generation: int,
        index_version: int,
        include_hidden: bool,
        people_service: object,
        pet_service: object,
        query_service: object | None,
        status_message: str | None,
        pet_status_message: str | None,
        signals: _DashboardWarmupSignals,
    ) -> None:
        super().__init__()
        self._root = root
        self._generation = int(generation)
        self._index_version = int(index_version)
        self._include_hidden = bool(include_hidden)
        self._people_service = people_service
        self._pet_service = pet_service
        self._query_service = query_service
        self._status_message = status_message
        self._pet_status_message = pet_status_message
        self._signals = signals

    def run(self) -> None:  # pragma: no cover - worker thread
        try:
            if self._query_service is not None:
                result = self._query_service.load_dashboard(self._include_hidden)
                summaries = result.people
                groups = result.groups
                pet_summaries = result.pets
                pending = result.pending_people
                pet_pending = result.pending_pets
            else:
                pet_summaries, pet_pending = self._pet_service.load_dashboard(
                    include_hidden=self._include_hidden
                )
                summaries, groups, pending = self._people_service.load_dashboard(
                    include_hidden=self._include_hidden,
                    pet_summaries=pet_summaries,
                )
            self._signals.ready.emit(
                _DashboardSnapshot(
                    self._root,
                    self._generation,
                    self._index_version,
                    self._include_hidden,
                    tuple(summaries),
                    tuple(groups),
                    tuple(pet_summaries),
                    int(pending),
                    int(pet_pending),
                    self._status_message,
                    self._pet_status_message,
                )
            )
        except Exception as exc:  # noqa: BLE001 - normal async failure boundary
            self._signals.failed.emit(self._generation, self._root, exc)


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
        recognition_query_getter: Callable[..., object | None] | None = None,
        recognition_merge_getter: Callable[..., object | None] | None = None,
        recognition_edit_getter: Callable[..., object | None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._detail = detail
        self._pinned_items_service = pinned_items_service
        self._library_root_getter = library_root_getter
        self._people_service_getter = people_service_getter
        self._pet_service_getter = pet_service_getter
        self._recognition_query_getter = recognition_query_getter
        self._recognition_merge_getter = recognition_merge_getter
        self._recognition_edit_getter = recognition_edit_getter
        self._cluster_callback = cluster_callback
        self._group_callback = group_callback
        self._pet_callback = pet_callback
        self._people_page = None
        self._people_service = None
        self._pet_service = None
        self._query_service = None
        self._dashboard_snapshot: _DashboardSnapshot | None = None
        self._warmup_root: Path | None = None
        self._warmup_generation = 0
        self._index_version = 0
        self._warmup_signals = _DashboardWarmupSignals(self)
        self._warmup_signals.ready.connect(self._on_dashboard_warmup_ready)
        self._warmup_signals.failed.connect(self._on_dashboard_warmup_failed)
        self._warmup_pool = QThreadPool(self)
        self._warmup_pool.setMaxThreadCount(1)
        self._warmup_pool.setThreadPriority(QThread.Priority.LowPriority)
        self._recognition_scans_requested = False
        self._people_view_shown = False
        self._first_viewport_ready = False
        self._is_shutting_down = False
        self._context.library.peopleIndexUpdated.connect(self._invalidate_dashboard_snapshot)
        self._context.library.petIndexUpdated.connect(self._invalidate_dashboard_snapshot)
        from iPhoto.gui.ui.tasks.manual_face_add_worker import ManualFaceAddWorker

        self._detail.configure_recognition_domain(
            manual_face_worker_factory=ManualFaceAddWorker,
        )
        self.rebind_library()

    def rebind_library(self) -> None:
        self._warmup_generation += 1
        self._warmup_root = None
        root = self._library_root_getter()
        people_service = self._people_service_getter(library_root=root)
        pet_service = self._pet_service_getter(library_root=root)
        query_service = (
            self._recognition_query_getter(library_root=root)
            if self._recognition_query_getter is not None
            else None
        )
        merge_service = (
            self._recognition_merge_getter(library_root=root)
            if self._recognition_merge_getter is not None
            else None
        )
        self._people_service = people_service
        self._pet_service = pet_service
        self._query_service = query_service
        self._merge_service = merge_service
        bind = getattr(self._context.library, "bind_recognition_services", None)
        if callable(bind):
            bind(people_service, pet_service)
        if self._dashboard_snapshot is not None and self._dashboard_snapshot.root != root:
            self._dashboard_snapshot = None
        self._recognition_scans_requested = False
        self._people_view_shown = False
        self._first_viewport_ready = False
        self._detail.set_people_service(people_service)
        self._detail.set_pet_service(pet_service)
        set_merge_service = getattr(self._detail, "set_recognition_merge_service", None)
        if callable(set_merge_service):
            set_merge_service(merge_service)
        set_query_service = getattr(self._detail, "set_recognition_query_service", None)
        if callable(set_query_service):
            set_query_service(query_service)
        set_edit_service = getattr(self._detail, "set_recognition_edit_service", None)
        if callable(set_edit_service):
            set_edit_service(
                self._recognition_edit_getter(library_root=root)
                if self._recognition_edit_getter is not None
                else None
            )
        self._detail.set_people_library_root(root)
        if self._people_page is not None:
            self._bind_services(
                self._people_page,
                root,
                people_service,
                pet_service,
                query_service,
                merge_service,
            )

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
        first_viewport_ready = getattr(people_page, "firstViewportReady", None)
        if first_viewport_ready is not None:
            first_viewport_ready.connect(self._mark_first_viewport_ready)
        self._bind_services(
            people_page,
            root,
            self._people_service_getter(library_root=root),
            self._pet_service_getter(library_root=root),
            (
                self._recognition_query_getter(library_root=root)
                if self._recognition_query_getter is not None
                else None
            ),
            (
                self._recognition_merge_getter(library_root=root)
                if self._recognition_merge_getter is not None
                else None
            ),
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

    def warm_dashboard_snapshot(self) -> None:
        """Read cached People/Pet summaries without creating widgets or AI models."""

        if self._is_shutting_down:
            return
        root = self._library_root_getter()
        if root is None or self._warmup_root == root:
            return
        if self._dashboard_snapshot is not None and self._dashboard_snapshot.root == root:
            return
        people_service = self._people_service_getter(library_root=root)
        pet_service = self._pet_service_getter(library_root=root)
        query_service = (
            self._recognition_query_getter(library_root=root)
            if self._recognition_query_getter is not None
            else None
        )
        if people_service is None or pet_service is None:
            return
        self._people_service = people_service
        self._pet_service = pet_service
        self._query_service = query_service
        self._warmup_root = root
        self._warmup_generation += 1
        generation = self._warmup_generation
        include_hidden = self._show_hidden_people_setting()
        library = self._context.library
        self._warmup_pool.start(
            _DashboardWarmupWorker(
                root=root,
                generation=generation,
                index_version=self._index_version,
                include_hidden=include_hidden,
                people_service=people_service,
                pet_service=pet_service,
                query_service=query_service,
                status_message=library.face_scan_status_message(),
                pet_status_message=library.pet_scan_status_message(),
                signals=self._warmup_signals,
            )
        )

    def _on_dashboard_warmup_ready(self, snapshot: object) -> None:
        if self._is_shutting_down:
            return
        if not isinstance(snapshot, _DashboardSnapshot):
            return
        self._warmup_root = None
        if (
            snapshot.root != self._library_root_getter()
            or snapshot.generation != self._warmup_generation
        ):
            return
        if snapshot.include_hidden != self._show_hidden_people_setting():
            self._reload_people_page()
            return
        self._dashboard_snapshot = snapshot
        if self._people_page is not None:
            self._apply_snapshot(self._people_page, snapshot)

    def _on_dashboard_warmup_failed(
        self,
        generation: int,
        root: object,
        _error: object,
    ) -> None:
        if generation == self._warmup_generation and root == self._warmup_root:
            self._warmup_root = None
            self._reload_people_page()

    def _invalidate_dashboard_snapshot(self) -> None:
        self._warmup_generation += 1
        self._index_version += 1
        self._warmup_root = None
        self._dashboard_snapshot = None
        invalidate = getattr(self._query_service, "invalidate", None)
        if callable(invalidate):
            invalidate()

    def _reload_people_page(self) -> None:
        reload_page = getattr(self._people_page, "reload", None)
        if callable(reload_page):
            reload_page(preserve_content=True)

    def _apply_snapshot(self, people_page: object, snapshot: _DashboardSnapshot) -> bool:
        apply_snapshot = getattr(people_page, "apply_snapshot", None)
        if not callable(apply_snapshot):
            return False
        return bool(
            apply_snapshot(
                library_root=snapshot.root,
                summaries=list(snapshot.summaries),
                groups=list(snapshot.groups),
                pet_summaries=list(snapshot.pet_summaries),
                pending=snapshot.pending,
                pet_pending=snapshot.pet_pending,
                status_message=snapshot.status_message,
                pet_status_message=snapshot.pet_status_message,
                index_version=snapshot.index_version,
            )
        )

    def _mark_first_viewport_ready(self, _generation: int = 0) -> None:
        self._first_viewport_ready = True
        self._request_recognition_scans()

    def people_view_shown(self) -> None:
        """Allow AI scans only after the user actually opens People."""

        self._people_view_shown = True
        self._request_recognition_scans()

    def _request_recognition_scans(self) -> None:
        if self._is_shutting_down or self._recognition_scans_requested:
            return
        if not self._people_view_shown or not self._first_viewport_ready:
            return
        self._recognition_scans_requested = True
        # Give cover delivery and the first paint a quiet window before model
        # inference starts competing for CPU and disk bandwidth.
        QTimer.singleShot(350, self._activate_recognition_scans)

    def _activate_recognition_scans(self) -> None:
        if self._is_shutting_down:
            return
        activate = getattr(self._context.library, "activate_recognition_scans", None)
        if callable(activate):
            activate()

    def _apply_people_page_preferences(self, people_page: object) -> None:
        setter = getattr(people_page, "set_show_hidden_people", None)
        if not callable(setter):
            return
        setter(self._show_hidden_people_setting())

    def _show_hidden_people_setting(self) -> bool:
        stored = self._context.settings.get("ui.show_hidden_people", False)
        if isinstance(stored, str):
            return stored.strip().lower() in {"1", "true", "yes", "on"}
        return bool(stored)

    def _bind_services(
        self,
        people_page: object,
        root: Path | None,
        people_service: object | None,
        pet_service: object | None,
        query_service: object | None,
        merge_service: object | None,
    ) -> None:
        snapshot = self._dashboard_snapshot
        snapshot_matches = (
            snapshot is not None
            and snapshot.root == root
            and snapshot.include_hidden == self._show_hidden_people_setting()
        )
        warmup_matches = self._warmup_root == root
        set_services = getattr(people_page, "set_services", None)
        if callable(set_services):
            set_services(
                people_service,
                pet_service,
                self._pinned_items_service,
                query_service=query_service,
                merge_service=merge_service,
                reload=not (snapshot_matches or warmup_matches),
            )
        elif people_service is not None and hasattr(people_page, "set_people_service"):
            people_page.set_people_service(people_service)
        elif hasattr(people_page, "set_library_root"):
            people_page.set_library_root(root)
        if not callable(set_services) and hasattr(people_page, "set_pet_service"):
            people_page.set_pet_service(pet_service)
        if not callable(set_services) and hasattr(people_page, "set_pinned_service"):
            people_page.set_pinned_service(self._pinned_items_service)
        people_page.set_status_message(self._context.library.face_scan_status_message())
        if hasattr(people_page, "set_pet_status_message"):
            people_page.set_pet_status_message(
                self._context.library.pet_scan_status_message()
            )
        if snapshot_matches and snapshot is not None:
            self._apply_snapshot(people_page, snapshot)

    def shutdown(self) -> None:
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        self._warmup_generation += 1
        self._warmup_root = None
        self._dashboard_snapshot = None
        self._warmup_pool.clear()
        self._warmup_pool.waitForDone(1500)


__all__ = ["RecognitionCoordinator"]
