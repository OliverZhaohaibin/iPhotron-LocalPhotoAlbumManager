from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtGui", reason="QtGui is required for GUI tests", exc_type=ImportError)

from PySide6.QtGui import QImage

from iPhoto.gui.detail_decode_backend import DecodedSurface
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailDecodeKey,
    DetailGeometryState,
    DetailRenderRequest,
)
from iPhoto.gui.detail_render_session import EditRenderState, PhotoRenderSessionHandle
from iPhoto.gui.ui.controllers.player_view_controller import (
    PlayerViewController,
    _AdjustmentPreparationSignals,
    _AdjustmentPreparationWorker,
    _PreparedRequestIntent,
    _StillSurfaceDecodeWorker,
)


def test_adjustment_preparation_repairs_legacy_source_revision(
    qapp,
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"source")
    signals = _AdjustmentPreparationSignals()
    prepared = []
    signals.ready.connect(lambda _key, state: prepared.append(state))
    worker = _AdjustmentPreparationWorker(
        ("asset-1", source),
        AssetSourceIdentity.create(source),
        signals,
        None,
        generation=3,
    )

    worker.run()

    assert len(prepared) == 1
    assert prepared[0].source_identity.has_stable_revision
    assert prepared[0].source_identity.revision[0] == "mtime"


def _request(source: Path, adjustments: dict | None = None) -> DetailRenderRequest:
    return DetailRenderRequest(
        generation=2,
        asset_id="asset-1",
        source_identity=AssetSourceIdentity.create(
            source,
            width=4000,
            height=3000,
            source_mtime_ns=1,
        ),
        viewport_physical_size=(1200, 900),
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState.from_adjustments(adjustments),
        reason="initial",
        raw_adjustments=adjustments or {},
        decode_level=2048,
    )


def _surface(request: DetailRenderRequest) -> DecodedSurface:
    image = QImage(1600, 1200, QImage.Format.Format_RGBA8888)
    return DecodedSurface(
        image=image,
        decode_key=DetailDecodeKey.from_request(request),
        source_size=(4000, 3000),
        decoded_size=(1600, 1200),
        decode_level=2048,
        backend="fake",
    )


def test_path_only_prefetch_uses_the_display_image_fallback_identity() -> None:
    source = Path("/tmp/photo.jpg")
    prepare = Mock(return_value=True)
    controller = SimpleNamespace(
        _viewport_metrics=Mock(return_value=((800, 600), 1.0)),
        _schedule_adjustment_preparation=prepare,
    )

    assert PlayerViewController.prefetch_image(controller, source)

    intent = prepare.call_args.args[0]
    assert intent.asset_id == ""
    assert intent.source_identity.path == source.absolute()
    assert intent.reason == "prefetch"


def test_zero_sized_viewport_waits_for_metrics_signal_without_timer_loop() -> None:
    intent = _PreparedRequestIntent(
        asset_id="asset-1",
        source_identity=AssetSourceIdentity.create(Path("/tmp/photo.jpg")),
        generation=7,
        reason="initial",
    )
    dispatch = Mock(return_value=True)
    metrics = Mock(side_effect=[None, ((1200, 900), 1.0)])
    controller = SimpleNamespace(
        _pending_layout_intent=(intent, {"Exposure": 0.2}),
        _request_generation=7,
        _viewport_metrics=metrics,
        _dispatch_prepared_intent=dispatch,
        _active_source_identity=None,
    )
    controller._retry_pending_layout_intent = lambda: (
        PlayerViewController._retry_pending_layout_intent(controller)
    )

    with patch(
        "iPhoto.gui.ui.controllers.player_view_controller.QTimer.singleShot"
    ) as single_shot:
        PlayerViewController._retry_pending_layout_intent(controller)
        assert controller._pending_layout_intent is not None
        PlayerViewController._on_viewport_metrics_changed(controller)

    single_shot.assert_not_called()
    assert controller._pending_layout_intent is None
    dispatch.assert_called_once_with(intent, {"Exposure": 0.2})


def test_clear_frame_cache_invalidates_old_library_requests_before_rebind() -> None:
    calls: list[object] = []
    controller = SimpleNamespace(
        cancel_pending_image_requests=Mock(
            side_effect=lambda: calls.append("cancel")
        ),
        _library_root_getter=Mock(return_value=Path("/library/new")),
        _decode_backend=SimpleNamespace(
            bind_library=Mock(side_effect=lambda root: calls.append(("bind", root)))
        ),
        _image_viewer=SimpleNamespace(
            clear_still_residency=Mock(side_effect=lambda: calls.append("clear_gpu"))
        ),
        _render_sessions={"old": object()},
        _current_render_session=object(),
        _render_session_interaction_depth={1: 1},
        _render_session_lod_pending={1},
        _render_session_pending_surfaces={1: object()},
    )

    PlayerViewController.clear_frame_cache(controller)

    assert calls == ["cancel", ("bind", Path("/library/new")), "clear_gpu"]
    assert controller._render_sessions == {}
    assert controller._current_render_session is None


def test_adjusted_image_worker_publishes_empty_raw_state() -> None:
    source = Path("/tmp/photo.jpg")
    signals = Mock()
    request = _request(source)
    backend = Mock(decode=Mock(return_value=_surface(request)))
    worker = _StillSurfaceDecodeWorker(request, signals, backend)
    worker.run()

    backend.decode.assert_called_once_with(request, worker)
    signals.completed.emit.assert_called_once_with(backend.decode.return_value)


def test_adjusted_image_worker_publishes_raw_adjustments_without_resolving() -> None:
    source = Path("/tmp/photo.jpg")
    signals = Mock()
    request = _request(source, {"Exposure": 0.5})
    surface = _surface(request)
    backend = Mock(decode=Mock(return_value=surface))
    worker = _StillSurfaceDecodeWorker(request, signals, backend)
    worker.run()

    signals.completed.emit.assert_called_once_with(surface)


def test_adjusted_image_worker_uses_viewport_backend_once() -> None:
    source = Path("/tmp/photo.jpg")
    signals = Mock()
    request = _request(source)
    surface = _surface(request)
    backend = Mock(decode=Mock(return_value=surface))
    worker = _StillSurfaceDecodeWorker(request, signals, backend)
    worker.run()

    backend.decode.assert_called_once_with(request, worker)
    assert surface.decoded_size == (1600, 1200)
    signals.completed.emit.assert_called_once_with(surface)


def test_still_worker_reports_decode_duration_for_playback_regression() -> None:
    source = Path("/tmp/photo.jpg")
    signals = Mock()
    request = _request(source)
    surface = _surface(request)
    backend = Mock(decode=Mock(return_value=surface))
    worker = _StillSurfaceDecodeWorker(request, signals, backend)

    with patch(
        "iPhoto.gui.ui.controllers.player_view_controller.emit_detail_event"
    ) as emit_event:
        worker.run()

    backend_event = next(
        call
        for call in emit_event.call_args_list
        if call.args == ("backend_selected",)
    )
    assert backend_event.kwargs["backend"] == "fake"
    assert backend_event.kwargs["duration_ms"] >= 0.0


def test_lod_upgrade_failure_preserves_the_presented_surface() -> None:
    source = Path("/tmp/photo.jpg")
    fail_current = Mock()
    controller = SimpleNamespace(
        _request_generation=7,
        _request_reason_by_generation={7: "zoom"},
        _loading_source=source,
        _loading_started_at=1.0,
        _active_asset_id="asset-1",
        _on_adjusted_image_failed=fail_current,
    )

    with patch(
        "iPhoto.gui.ui.controllers.player_view_controller.emit_detail_event"
    ) as emit_event:
        PlayerViewController._on_scheduled_image_failed(
            controller,
            7,
            source,
            "higher LOD failed",
        )

    fail_current.assert_not_called()
    assert controller._loading_source is None
    assert controller._loading_started_at is None
    emit_event.assert_called_once_with(
        "lod_upgrade_failed",
        generation=7,
        asset_id="asset-1",
        reason="zoom",
        message="higher LOD failed",
    )


def test_resident_live_photo_still_is_deferred_while_motion_is_visible() -> None:
    source = Path("/tmp/live-photo.heic")
    request = _request(source)
    surface = _surface(request)
    session = SimpleNamespace(
        baseline_state=SimpleNamespace(raw_adjustments={}),
        edit_state=SimpleNamespace(
            color_stats=surface.color_stats,
            shader_adjustments={"Exposure": 0.25},
        ),
        current_surface=surface,
        activate_surface=Mock(return_value=True),
    )
    video_area = object()
    image_viewer = SimpleNamespace(activate_resident_surface=Mock(return_value=True))
    scheduler = SimpleNamespace(request=Mock(return_value=True))
    controller = SimpleNamespace(
        _viewport_metrics=Mock(return_value=((1200, 900), 1.0)),
        _texture_limit=Mock(return_value=8192),
        _current_decode_level=None,
        _active_asset_id="",
        _active_source_identity=None,
        _active_adjustments={},
        _request_reason_by_generation={},
        _session_for_request=Mock(return_value=session),
        _defer_still_updates=True,
        _player_stack=SimpleNamespace(currentWidget=Mock(return_value=video_area)),
        _video_area=video_area,
        _image_viewer=image_viewer,
        _still_scheduler=scheduler,
        _loading_started_at=1.0,
        _loading_source=source,
        _pending_still=None,
        _touch_render_session=Mock(),
        show_image_surface=Mock(),
    )
    intent = _PreparedRequestIntent(
        asset_id="asset-1",
        source_identity=request.source_identity,
        generation=2,
        reason="initial",
    )

    assert PlayerViewController._dispatch_prepared_intent(controller, intent, {})

    assert controller._pending_still == (surface, {"Exposure": 0.25})
    image_viewer.activate_resident_surface.assert_not_called()
    controller.show_image_surface.assert_not_called()
    scheduler.request.assert_not_called()


def test_committed_rotation_updates_current_session_without_reload() -> None:
    source = Path("/tmp/photo.jpg")
    request = _request(source)
    surface = _surface(request)
    state = EditRenderState.create(
        {},
        color_stats=surface.color_stats,
        revision=("index", 1),
    )
    session = PhotoRenderSessionHandle(
        session_id=3,
        asset_id="asset-1",
        source_identity=request.source_identity,
        current_surface=surface,
        edit_state=state,
        baseline_state=state,
    )
    controller = SimpleNamespace(
        _current_render_session=session,
        _active_adjustments={},
        _image_viewer=SimpleNamespace(set_adjustments=Mock()),
        _video_area=SimpleNamespace(current_source=Mock(return_value=None)),
        _request_generation=2,
        _queue_render_session_lod=Mock(),
    )

    assert PlayerViewController.apply_committed_adjustments(
        controller,
        source,
        {"Crop_Rotate90": 3.0},
        "rotate",
    )

    assert session.edit_state is session.baseline_state
    assert session.edit_state.revision[0] == "commit"
    assert session.edit_state.raw_adjustments["Crop_Rotate90"] == 3.0
    controller._image_viewer.set_adjustments.assert_called_once()
    controller._queue_render_session_lod.assert_called_once_with(session)


def test_gpu_allocation_failure_retries_initial_surface_at_lower_lod() -> None:
    source = Path("/tmp/photo.jpg")
    identity = AssetSourceIdentity.create(source, width=4000, height=3000)
    failed_key = DetailDecodeKey(
        asset_id="asset-1",
        source=source.absolute(),
        source_revision=identity.revision,
        orientation=1,
        decode_level=3072,
    )
    scheduler = SimpleNamespace(request=Mock(return_value=True))
    controller = SimpleNamespace(
        _request_generation=7,
        _request_reason_by_generation={7: "initial"},
        _last_presented_decode_key=None,
        _render_sessions={},
        _image_viewer=SimpleNamespace(),
        _active_asset_id="asset-1",
        _active_source_identity=identity,
        _active_adjustments={},
        _viewport_metrics=Mock(return_value=((1200, 900), 1.0)),
        _texture_limit=Mock(return_value=8192),
        _still_scheduler=scheduler,
        _loading_source=None,
        _loading_started_at=None,
        imageLoadingFailed=Mock(),
    )

    PlayerViewController._on_still_texture_allocation_failed(
        controller,
        failed_key,
        7,
        "create_failed",
    )

    fallback = scheduler.request.call_args.args[0]
    assert fallback.decode_level == 2048
    assert fallback.generation == 7
    assert controller._loading_source == source.absolute()
    controller.imageLoadingFailed.emit.assert_not_called()
