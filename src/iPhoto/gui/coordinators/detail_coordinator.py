"""Detail-domain coordinator and immersive-window port."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject


class DetailCoordinator(QObject):
    """Expose Detail/Playback without routing consumers through the runtime."""

    def __init__(
        self,
        *,
        router,
        playback,
        edit_provider: Callable[[], object],
        detail_viewmodel=None,
        asset_state_service_getter: Callable[[], object | None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._router = router
        self._playback = playback
        self._edit_provider = edit_provider
        self._detail_viewmodel = detail_viewmodel
        self._asset_state_service_getter = asset_state_service_getter

    @property
    def playback(self):
        return self._playback

    def play_asset(self, row: int) -> None:
        self._playback.play_asset(row)

    def reset_for_gallery(self) -> None:
        self._playback.reset_for_gallery()

    def rebind_library(
        self,
        library_epoch: int | None = None,
        *,
        session_changed: bool = True,
    ) -> None:
        """Rebind Detail state without touching optional recognition/location domains."""
        rebind_playback = getattr(self._playback, "rebind_library", None)
        if callable(rebind_playback):
            rebind_playback(library_epoch, session_changed=session_changed)
        if self._detail_viewmodel is None or self._asset_state_service_getter is None:
            return
        self._detail_viewmodel.bind_asset_state_service(
            self._asset_state_service_getter()
        )

    def is_edit_view_active(self) -> bool:
        return self._router.is_edit_view_active()

    def edit_controller(self):
        return self._edit_provider()

    def suspend_playback_for_transition(self) -> bool:
        return self._playback.suspend_playback_for_transition()

    def prepare_fullscreen_asset(self) -> bool:
        return self._playback.prepare_fullscreen_asset()

    def show_placeholder_in_viewer(self) -> None:
        self._playback.show_placeholder_in_viewer()

    def resume_playback_after_transition(self) -> None:
        self._playback.resume_playback_after_transition()

    # Optional-domain ports -------------------------------------------------
    def set_people_service(self, service: object | None) -> None:
        self._playback.set_people_service(service)

    def set_pet_service(self, service: object | None) -> None:
        self._playback.set_pet_service(service)

    def set_recognition_query_service(self, service: object | None) -> None:
        self._playback.set_recognition_query_service(service)

    def set_recognition_edit_service(self, service: object | None) -> None:
        self._playback.set_recognition_edit_service(service)

    def set_recognition_merge_service(self, service: object | None) -> None:
        self._playback.set_recognition_merge_service(service)

    def set_people_library_root(self, root) -> None:
        self._playback.set_people_library_root(root)

    def set_face_name_display_enabled(self, enabled: bool) -> None:
        self._playback.set_face_name_display_enabled(enabled)

    def handle_people_snapshot_committed(self, event: object) -> None:
        self._playback.handle_people_snapshot_committed(event)

    def set_location_write_queue(self, queue: object | None) -> None:
        self._playback.set_location_write_queue(queue)

    def set_info_panel(self, panel: object) -> None:
        self._playback.set_info_panel(panel)

    def set_map_runtime(self, runtime: object | None) -> None:
        self._playback.set_map_runtime(runtime)

    def toggle_info_panel(self) -> None:
        self._playback.toggle_info_panel()

    def configure_location_domain(self, **dependencies) -> None:
        self._playback.configure_location_domain(**dependencies)

    def configure_recognition_domain(self, **dependencies) -> None:
        self._playback.configure_recognition_domain(**dependencies)


__all__ = ["DetailCoordinator"]
