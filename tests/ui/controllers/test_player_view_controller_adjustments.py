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
    _AdjustedImageWorker,
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


def test_adjusted_image_worker_skips_color_stats_without_adjustments() -> None:
    source = Path("/tmp/photo.jpg")
    signals = Mock()
    request = _request(source)
    backend = Mock(decode=Mock(return_value=_surface(request)))
    with patch(
        "iPhoto.gui.ui.controllers.player_view_controller.compute_color_statistics",
    ) as compute_stats:
        worker = _AdjustedImageWorker(request, signals, backend)
        worker.run()

    backend.decode.assert_called_once_with(request, worker)
    compute_stats.assert_not_called()
    signals.completed.emit.assert_called_once_with(backend.decode.return_value, {})


def test_adjusted_image_worker_resolves_prepared_adjustments() -> None:
    source = Path("/tmp/photo.jpg")
    signals = Mock()
    request = _request(source, {"Exposure": 0.5})
    surface = _surface(request)
    backend = Mock(decode=Mock(return_value=surface))
    with patch(
        "iPhoto.gui.ui.controllers.player_view_controller.compute_color_statistics",
        return_value="stats",
    ) as compute_stats:
        with patch(
            "iPhoto.gui.ui.controllers.player_view_controller.resolve_adjustment_mapping",
            return_value={"Exposure": 0.5},
        ) as resolve:
            worker = _AdjustedImageWorker(request, signals, backend)
            worker.run()

    compute_stats.assert_called_once_with(surface.image)
    resolve.assert_called_once_with(
        {"Exposure": 0.5},
        stats="stats",
        normalize_bw_for_render=True,
    )
    signals.completed.emit.assert_called_once_with(surface, {"Exposure": 0.5})


def test_adjusted_image_worker_uses_viewport_backend_once() -> None:
    source = Path("/tmp/photo.jpg")
    signals = Mock()
    request = _request(source)
    surface = _surface(request)
    backend = Mock(decode=Mock(return_value=surface))
    worker = _AdjustedImageWorker(request, signals, backend)
    worker.run()

    backend.decode.assert_called_once_with(request, worker)
    assert surface.decoded_size == (1600, 1200)
    signals.completed.emit.assert_called_once_with(surface, {})


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
