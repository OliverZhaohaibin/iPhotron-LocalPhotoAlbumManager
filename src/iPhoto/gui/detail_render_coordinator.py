"""GUI-owned lifecycle coordinator for Detail still and video transactions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from PySide6.QtCore import QObject, Signal

from iPhoto.gui.detail_pipeline import DetailRenderTransaction
from iPhoto.gui.detail_profile import emit_detail_event


class DetailRenderState(str, Enum):
    CREATED = "created"
    ROUTED = "routed"
    PREPARING = "preparing"
    PRESENTED = "presented"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATES = {
    DetailRenderState.PRESENTED,
    DetailRenderState.FAILED,
    DetailRenderState.CANCELLED,
}

DetailSurfaceKind = Literal["still", "video_frame", "live_motion_frame", "live_still"]


class DetailSurfacePresentationResult(str, Enum):
    """Classify lifecycle bookkeeping for one visible surface delivery."""

    REJECTED_STALE = "rejected_stale"
    NEW_SURFACE = "new_surface"
    DUPLICATE_CURRENT_SURFACE = "duplicate_current_surface"


@dataclass(frozen=True, slots=True)
class DetailRenderSnapshot:
    transaction: DetailRenderTransaction
    state: DetailRenderState
    message: str = ""
    presented_surfaces: tuple[DetailSurfaceKind, ...] = ()


class DetailRenderCoordinator(QObject):
    """Own the active transaction and enforce one current terminal result."""

    stateChanged = Signal(object)
    presented = Signal(object)
    failed = Signal(object)
    cancelled = Signal(object)
    surfacePresented = Signal(object, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._snapshot: DetailRenderSnapshot | None = None

    @property
    def snapshot(self) -> DetailRenderSnapshot | None:
        return self._snapshot

    @property
    def current_generation(self) -> int:
        snapshot = self._snapshot
        return snapshot.transaction.generation if snapshot is not None else 0

    def is_current(self, generation: int) -> bool:
        snapshot = self._snapshot
        return bool(
            snapshot is not None
            and snapshot.transaction.generation == int(generation)
            and snapshot.state not in _TERMINAL_STATES
        )

    def owns_generation(self, generation: int) -> bool:
        """Return whether the transaction still owns follow-on surface delivery."""

        snapshot = self._snapshot
        return bool(
            snapshot is not None
            and snapshot.transaction.generation == int(generation)
            and snapshot.state
            not in {DetailRenderState.FAILED, DetailRenderState.CANCELLED}
        )

    def begin(self, transaction: DetailRenderTransaction) -> bool:
        if transaction.generation <= 0:
            raise ValueError("Detail render transaction generation must be positive")
        current = self._snapshot
        if current is not None:
            if current.transaction == transaction:
                return False
            if current.state not in _TERMINAL_STATES:
                self._transition(DetailRenderState.CANCELLED)
        self._snapshot = DetailRenderSnapshot(transaction, DetailRenderState.CREATED)
        self.stateChanged.emit(self._snapshot)
        return True

    def mark_surface_presented(
        self,
        generation: int,
        surface_kind: DetailSurfaceKind,
    ) -> DetailSurfacePresentationResult:
        """Record one visible surface while keeping one terminal transaction."""

        snapshot = self._snapshot
        if snapshot is None or snapshot.transaction.generation != int(generation):
            return DetailSurfacePresentationResult.REJECTED_STALE
        if snapshot.state in {DetailRenderState.FAILED, DetailRenderState.CANCELLED}:
            return DetailSurfacePresentationResult.REJECTED_STALE
        allowed_surfaces = {
            "image": {"still"},
            "video": {"video_frame"},
            "live_motion": {"live_motion_frame", "live_still"},
        }.get(snapshot.transaction.media_kind, set())
        if surface_kind not in allowed_surfaces:
            return DetailSurfacePresentationResult.REJECTED_STALE
        if surface_kind in snapshot.presented_surfaces:
            return DetailSurfacePresentationResult.DUPLICATE_CURRENT_SURFACE
        if snapshot.state is DetailRenderState.PRESENTED and (
            snapshot.transaction.media_kind != "live_motion"
            or surface_kind not in {"live_motion_frame", "live_still"}
        ):
            return DetailSurfacePresentationResult.REJECTED_STALE
        self._snapshot = DetailRenderSnapshot(
            transaction=snapshot.transaction,
            state=snapshot.state,
            message=snapshot.message,
            presented_surfaces=(*snapshot.presented_surfaces, surface_kind),
        )
        emit_detail_event(
            "surface_presented",
            generation=int(generation),
            media_type=snapshot.transaction.media_kind,
            surface_kind=surface_kind,
        )
        if snapshot.state is not DetailRenderState.PRESENTED:
            if not self.mark_presented(generation):
                return DetailSurfacePresentationResult.REJECTED_STALE
        current = self._snapshot
        if current is not None:
            self.surfacePresented.emit(current, surface_kind)
        return DetailSurfacePresentationResult.NEW_SURFACE

    def mark_routed(self, generation: int, *, row: int) -> bool:
        if not self._can_transition(generation, {DetailRenderState.CREATED}):
            return False
        transaction = self._snapshot.transaction  # type: ignore[union-attr]
        emit_detail_event(
            "route_visible",
            generation=transaction.generation,
            row=int(row),
            media_type=transaction.media_kind,
        )
        self._transition(DetailRenderState.ROUTED)
        return True

    def mark_preparing(self, generation: int) -> bool:
        if not self._can_transition(
            generation,
            {DetailRenderState.CREATED, DetailRenderState.ROUTED},
        ):
            return False
        self._transition(DetailRenderState.PREPARING)
        return True

    def mark_presented(self, generation: int) -> bool:
        if not self._can_transition(
            generation,
            {
                DetailRenderState.CREATED,
                DetailRenderState.ROUTED,
                DetailRenderState.PREPARING,
            },
        ):
            return False
        transaction = self._snapshot.transaction  # type: ignore[union-attr]
        emit_detail_event(
            "presented",
            generation=transaction.generation,
            media_type=transaction.media_kind,
        )
        if transaction.media_kind in {"video", "live_motion"}:
            emit_detail_event(
                "video_first_frame_presented",
                generation=transaction.generation,
                media_type=transaction.media_kind,
            )
        self._transition(DetailRenderState.PRESENTED)
        return True

    def mark_failed(self, generation: int, message: str) -> bool:
        if not self.is_current(generation):
            return False
        emit_detail_event("failed", generation=int(generation), message=str(message))
        self._transition(DetailRenderState.FAILED, message=str(message))
        return True

    def cancel_current(self) -> bool:
        snapshot = self._snapshot
        if snapshot is None or snapshot.state in _TERMINAL_STATES:
            return False
        self._transition(DetailRenderState.CANCELLED)
        return True

    def reset(self) -> None:
        """Forget the active transaction after invalidating any live work."""

        self.cancel_current()
        self._snapshot = None

    def _can_transition(
        self,
        generation: int,
        allowed: set[DetailRenderState],
    ) -> bool:
        snapshot = self._snapshot
        return bool(
            snapshot is not None
            and snapshot.transaction.generation == int(generation)
            and snapshot.state in allowed
        )

    def _transition(self, state: DetailRenderState, *, message: str = "") -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        self._snapshot = DetailRenderSnapshot(
            snapshot.transaction,
            state,
            message,
            snapshot.presented_surfaces,
        )
        emit = {
            DetailRenderState.PRESENTED: self.presented,
            DetailRenderState.FAILED: self.failed,
            DetailRenderState.CANCELLED: self.cancelled,
        }.get(state)
        if state == DetailRenderState.CANCELLED:
            emit_detail_event("cancelled", generation=snapshot.transaction.generation)
        self.stateChanged.emit(self._snapshot)
        if emit is not None:
            emit.emit(self._snapshot)


__all__ = [
    "DetailRenderCoordinator",
    "DetailRenderSnapshot",
    "DetailRenderState",
    "DetailSurfaceKind",
    "DetailSurfacePresentationResult",
]
