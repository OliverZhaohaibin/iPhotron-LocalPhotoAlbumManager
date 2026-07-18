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
from iPhoto.gui.ui.controllers.player_view_controller import (
    PlayerViewController,
    _StillSurfaceDecodeWorker,
    _PreparedRequestIntent,
)


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
