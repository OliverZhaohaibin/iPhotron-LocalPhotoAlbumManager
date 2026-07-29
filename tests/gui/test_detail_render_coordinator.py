from __future__ import annotations

from pathlib import Path

from iPhoto.gui.detail_pipeline import AssetSourceIdentity, DetailRenderTransaction
from iPhoto.gui.detail_render_coordinator import DetailRenderCoordinator, DetailRenderState


def _transaction(generation: int, *, media_kind: str = "image") -> DetailRenderTransaction:
    return DetailRenderTransaction(
        generation=generation,
        asset_id=f"asset-{generation}",
        media_kind=media_kind,
        source_identity=AssetSourceIdentity.create(Path(f"/{generation}.jpg")),
        viewport_physical_size=(1200, 800),
        device_pixel_ratio=2.0,
    )


def test_transaction_coordinator_allows_exactly_one_presented_terminal(qapp) -> None:
    coordinator = DetailRenderCoordinator()
    presented = []
    coordinator.presented.connect(presented.append)

    assert coordinator.begin(_transaction(1))
    assert coordinator.mark_routed(1, row=2)
    assert coordinator.mark_preparing(1)
    assert coordinator.mark_presented(1)
    assert not coordinator.mark_presented(1)
    assert not coordinator.mark_failed(1, "late")
    assert coordinator.snapshot is not None
    assert coordinator.snapshot.state is DetailRenderState.PRESENTED
    assert len(presented) == 1


def test_new_transaction_cancels_old_and_rejects_stale_result(qapp) -> None:
    coordinator = DetailRenderCoordinator()
    cancelled = []
    coordinator.cancelled.connect(cancelled.append)

    coordinator.begin(_transaction(1))
    coordinator.mark_preparing(1)
    coordinator.begin(_transaction(2, media_kind="video"))

    assert len(cancelled) == 1
    assert cancelled[0].transaction.generation == 1
    assert not coordinator.mark_presented(1)
    assert coordinator.mark_routed(2, row=3)
    assert coordinator.mark_presented(2)


def test_live_motion_and_restored_still_share_one_completed_transaction(qapp) -> None:
    coordinator = DetailRenderCoordinator()
    terminal_presentations = []
    surfaces = []
    coordinator.presented.connect(terminal_presentations.append)
    coordinator.surfacePresented.connect(
        lambda snapshot, kind: surfaces.append((snapshot.transaction.generation, kind))
    )
    transaction = _transaction(7, media_kind="live_motion")

    assert coordinator.begin(transaction)
    assert coordinator.mark_preparing(7)
    assert coordinator.mark_surface_presented(7, "live_motion_frame")
    assert coordinator.snapshot is not None
    assert coordinator.snapshot.state is DetailRenderState.PRESENTED
    assert coordinator.owns_generation(7)
    assert coordinator.mark_surface_presented(7, "live_still")
    assert not coordinator.begin(transaction)

    assert len(terminal_presentations) == 1
    assert surfaces == [(7, "live_motion_frame"), (7, "live_still")]
    assert coordinator.snapshot.presented_surfaces == (
        "live_motion_frame",
        "live_still",
    )


def test_live_still_surface_rejects_stale_generation(qapp) -> None:
    coordinator = DetailRenderCoordinator()
    coordinator.begin(_transaction(1, media_kind="live_motion"))
    coordinator.mark_surface_presented(1, "live_motion_frame")
    coordinator.begin(_transaction(2, media_kind="live_motion"))

    assert not coordinator.mark_surface_presented(1, "live_still")
    assert coordinator.snapshot is not None
    assert coordinator.snapshot.transaction.generation == 2


def test_still_request_is_derived_from_transaction(tmp_path: Path) -> None:
    from iPhoto.gui.detail_pipeline import DetailGeometryState, DetailRenderRequest

    transaction = DetailRenderTransaction(
        generation=7,
        asset_id="asset-7",
        media_kind="image",
        source_identity=AssetSourceIdentity.create(
            tmp_path / "photo.jpg",
            width=4000,
            height=3000,
        ),
        viewport_physical_size=(1600, 1000),
        device_pixel_ratio=2.0,
    )
    request = DetailRenderRequest.from_transaction(
        transaction,
        geometry=DetailGeometryState(),
        reason="initial",
    )

    assert request.generation == transaction.generation
    assert request.asset_id == transaction.asset_id
    assert request.viewport_physical_size == transaction.viewport_physical_size
