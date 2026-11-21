"""Narrow ports shared by desktop coordinators and their UI consumers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QModelIndex


class CoordinatorLifecyclePort(Protocol):
    """Lifecycle surface owned by the desktop coordinator runtime."""

    def start(self) -> None: ...

    def shutdown(self) -> None: ...


class GalleryWindowPort(Protocol):
    """Gallery operations consumed by the startup chain and main window."""

    def startup_model(self): ...

    def open_album_from_path(self, path: Path) -> None: ...

    def paths_from_indexes(self, indexes: Iterable[QModelIndex]) -> list[Path]: ...


class DetailNavigationPort(Protocol):
    """Detail operations used by gallery navigation."""

    def play_asset(self, row: int) -> None: ...

    def reset_for_gallery(self) -> None: ...


class ImmersiveDetailPort(Protocol):
    """Detail operations required by the frameless window manager."""

    def is_edit_view_active(self) -> bool: ...

    def edit_controller(self): ...

    def suspend_playback_for_transition(self) -> bool: ...

    def prepare_fullscreen_asset(self) -> bool: ...

    def show_placeholder_in_viewer(self) -> None: ...

    def resume_playback_after_transition(self) -> None: ...


class RecognitionDetailPort(Protocol):
    """Recognition operations exposed by Detail without leaking Playback."""

    def set_people_service(self, service: object | None) -> None: ...

    def set_pet_service(self, service: object | None) -> None: ...

    def set_recognition_query_service(self, service: object | None) -> None: ...

    def set_recognition_edit_service(self, service: object | None) -> None: ...

    def set_recognition_merge_service(self, service: object | None) -> None: ...

    def set_people_library_root(self, root: Path | None) -> None: ...

    def set_face_name_display_enabled(self, enabled: bool) -> None: ...

    def handle_people_snapshot_committed(self, event: object) -> None: ...


class LocationInfoDetailPort(Protocol):
    """Info/location operations exposed by Detail playback."""

    def set_location_write_queue(self, queue: object | None) -> None: ...

    def set_info_panel(self, panel: object) -> None: ...

    def set_map_runtime(self, runtime: object | None) -> None: ...

    def toggle_info_panel(self) -> None: ...


__all__ = [
    "CoordinatorLifecyclePort",
    "DetailNavigationPort",
    "GalleryWindowPort",
    "ImmersiveDetailPort",
    "LocationInfoDetailPort",
    "RecognitionDetailPort",
]
