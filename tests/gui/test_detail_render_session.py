from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtGui import QImage

from iPhoto.core.color_resolver import ColorStats
from iPhoto.gui.detail_decode_backend import DecodedSurface
from iPhoto.gui.detail_pipeline import AssetSourceIdentity, DetailDecodeKey
from iPhoto.gui.detail_render_session import (
    EditRenderState,
    PhotoRenderSessionHandle,
    SurfaceRetentionBudget,
)
from iPhoto.gui.ui.controllers.player_view_controller import PlayerViewController


def _surface(path: Path, *, side: int = 4, level: int | str = "full") -> DecodedSurface:
    identity = AssetSourceIdentity.create(
        path,
        size_bytes=side * side * 4,
        source_mtime_ns=1,
        width=side,
        height=side,
    )
    image = QImage(side, side, QImage.Format.Format_RGBA8888)
    return DecodedSurface(
        image=image,
        decode_key=DetailDecodeKey(
            asset_id=path.stem,
            source=identity.path,
            source_revision=identity.revision,
            orientation=1,
            decode_level=level,
        ),
        source_size=(side, side),
        decoded_size=(side, side),
        decode_level=level,
        backend="test",
    )


def _handle(path: Path) -> PhotoRenderSessionHandle:
    surface = _surface(path)
    identity = AssetSourceIdentity.create(path, source_mtime_ns=1)
    state = EditRenderState.create({}, color_stats=ColorStats(), revision=("index", 1))
    return PhotoRenderSessionHandle(
        session_id=1,
        asset_id=path.stem,
        source_identity=identity,
        current_surface=surface,
        edit_state=state,
        baseline_state=state,
    )


def test_handle_releases_lower_lod_after_upload_and_invalidates_safely(tmp_path: Path) -> None:
    handle = _handle(tmp_path / "photo.jpg")
    low = handle.current_surface
    high = _surface(tmp_path / "photo.jpg", side=8, level=4096)

    assert handle.replace_surface(high, retain_upload_fallback=True)
    assert handle.upload_fallback is low
    handle.finish_upload()
    assert handle.upload_fallback is None
    assert handle.current_surface is high

    handle.invalidate()
    assert handle.current_surface is None
    assert handle.next_state({"Exposure": 1.0}) is None
    assert handle.restore_baseline() is None


def test_surface_budget_never_accepts_more_than_its_hard_limit(tmp_path: Path) -> None:
    budget = SurfaceRetentionBudget(128)
    first = _surface(tmp_path / "first.jpg")
    second = _surface(tmp_path / "second.jpg")

    assert budget.replace(1, budget.surface_bytes(first))
    assert budget.replace(2, budget.surface_bytes(second))
    assert budget.retained_bytes == 128
    assert not budget.replace(3, 1)
    assert budget.retained_bytes == 128


def test_player_view_evicts_sessions_to_keep_one_cpu_surface_budget(tmp_path: Path) -> None:
    controller = PlayerViewController.__new__(PlayerViewController)
    controller._surface_budget = SurfaceRetentionBudget(128)
    controller._render_sessions = OrderedDict()
    controller._current_render_session = None
    controller._next_render_session_id = 1
    handles = []

    for index in range(3):
        path = tmp_path / f"photo-{index}.jpg"
        image = QImage(4, 4, QImage.Format.Format_RGBA8888)
        identity = AssetSourceIdentity.create(path, source_mtime_ns=index + 1)
        handles.append(
            PlayerViewController._retain_render_session(
                controller,
                source=path,
                image=image,
                adjustments={},
                identity=identity,
                color_stats=ColorStats(),
                asset_id=f"asset-{index}",
            )
        )

    assert handles[0] is not None and not handles[0].valid
    assert handles[1] is not None and handles[1].valid
    assert handles[2] is not None and handles[2].valid
    assert controller.retained_surface_bytes == 128
    assert len(controller._render_sessions) == 2

    oversized = QImage(10, 10, QImage.Format.Format_RGBA8888)
    rejected = PlayerViewController._retain_render_session(
        controller,
        source=tmp_path / "oversized.jpg",
        image=oversized,
        adjustments={},
        identity=AssetSourceIdentity.create(
            tmp_path / "oversized.jpg",
            source_mtime_ns=9,
        ),
        color_stats=ColorStats(),
        asset_id="oversized",
    )
    assert rejected is None
    assert controller.retained_surface_bytes == 128


def test_deferred_still_uses_same_surface_budget() -> None:
    controller = PlayerViewController.__new__(PlayerViewController)
    controller._surface_budget = SurfaceRetentionBudget(128)
    controller._render_sessions = OrderedDict()
    controller._current_render_session = None

    assert PlayerViewController._reserve_pending_surface(
        controller,
        QImage(4, 4, QImage.Format.Format_RGBA8888),
    )
    assert controller.retained_surface_bytes == 64
    assert not PlayerViewController._reserve_pending_surface(
        controller,
        QImage(10, 10, QImage.Format.Format_RGBA8888),
    )
    assert controller.retained_surface_bytes == 0


def test_stale_shared_edit_handle_returns_safely_instead_of_raising(tmp_path: Path) -> None:
    controller = PlayerViewController.__new__(PlayerViewController)
    controller._surface_budget = SurfaceRetentionBudget(1024)
    controller._render_sessions = OrderedDict()
    controller._current_render_session = None
    controller._next_render_session_id = 1
    path = tmp_path / "edit.jpg"
    handle = PlayerViewController._retain_render_session(
        controller,
        source=path,
        image=QImage(4, 4, QImage.Format.Format_RGBA8888),
        adjustments={},
        identity=AssetSourceIdentity.create(path, source_mtime_ns=1),
        color_stats=ColorStats(),
        asset_id="edit",
    )
    assert handle is not None
    acquired = PlayerViewController.acquire_render_session(controller, path)
    assert acquired is handle

    handle.invalidate()

    assert PlayerViewController.update_render_session(controller, handle, {}) is None
    assert not PlayerViewController.finish_render_session(
        controller,
        handle,
        committed=False,
    )
