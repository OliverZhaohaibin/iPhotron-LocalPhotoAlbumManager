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
            and snapshot.state not in {DetailRenderState.FAILED, DetailRenderState.CANCELLED}
        )

    def begin(self, transaction: DetailRenderTransaction) -> bool:
        if transaction.generation <= 0:
            raise ValueError("Detail render transaction generation must be positive")
        current = self._snapshot
        if current is not None:
            if current.transaction == transaction and (
                current.state not in _TERMINAL_STATES
                or transaction.media_kind == "live_motion"
            ):
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
    ) -> bool:
        """Record a visible surface without reopening a completed transaction.

        A Live Photo transaction becomes visible on its motion first frame but
        continues to own the later still restoration.  That still is a second
        surface presentation, not a second transaction terminal transition.
        """

        snapshot = self._snapshot
        if snapshot is None or snapshot.transaction.generation != int(generation):
            return False
        if snapshot.state in {DetailRenderState.FAILED, DetailRenderState.CANCELLED}:
            return False
        if snapshot.state is DetailRenderState.PRESENTED:
            if snapshot.transaction.media_kind != "live_motion" or surface_kind not in {
                "live_motion_frame",
                "live_still",
            }:
                return False
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
                return False
        current = self._snapshot
        if current is not None:
            self.surfacePresented.emit(current, surface_kind)
        return True

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
]
