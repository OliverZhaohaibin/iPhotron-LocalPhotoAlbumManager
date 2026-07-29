from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtGui", reason="QtGui is required for GUI tests", exc_type=ImportError)

from PySide6.QtGui import QImage

from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailRenderTransaction,
    PlaybackAsyncToken,
)
from iPhoto.gui.ui.controllers.player_view_controller import (
    PlayerViewController,
    _AdjustedImageWorker,
)


def test_adjusted_image_worker_collects_shared_stats_without_sidecar() -> None:
    source = Path("/tmp/photo.jpg")
    signals = Mock()
    edit_service = Mock()
    edit_service.sidecar_exists.return_value = False
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)

    with patch(
        "iPhoto.gui.ui.controllers.player_view_controller.image_loader.load_qimage",
        return_value=image,
    ), patch(
        "iPhoto.gui.ui.controllers.player_view_controller.compute_color_statistics",
    ) as compute_stats:
        worker = _AdjustedImageWorker(source, signals, edit_service)
        worker.run()

    edit_service.describe_adjustments.assert_not_called()
    compute_stats.assert_called_once_with(image)
    signals.completed.emit.assert_called_once_with(source, image, {})


def test_adjusted_image_worker_resolves_adjustments_when_sidecar_exists() -> None:
    source = Path("/tmp/photo.jpg")
    signals = Mock()
    edit_service = Mock()
    edit_service.sidecar_exists.return_value = True
    edit_service.describe_adjustments.return_value = Mock(
        resolved_adjustments={"Exposure": 0.5},
    )
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)

    with patch(
        "iPhoto.gui.ui.controllers.player_view_controller.image_loader.load_qimage",
        return_value=image,
    ), patch(
        "iPhoto.gui.ui.controllers.player_view_controller.compute_color_statistics",
        return_value="stats",
    ) as compute_stats:
        worker = _AdjustedImageWorker(source, signals, edit_service)
        worker.run()

    compute_stats.assert_called_once_with(image)
    edit_service.describe_adjustments.assert_called_once_with(source, color_stats="stats")
    signals.completed.emit.assert_called_once_with(source, image, {"Exposure": 0.5})


def test_deferred_still_keeps_original_live_transaction_until_applied() -> None:
    source = Path("/tmp/live.heic")
    transaction = DetailRenderTransaction(
        generation=9,
        asset_id="live-asset",
        media_kind="live_motion",
        source_identity=AssetSourceIdentity.create(source, source_mtime_ns=1),
    )
    video_surface = object()
    controller = SimpleNamespace(
        _loading_source=source,
        _loading_started_at=None,
        _loading_transaction=transaction,
        _defer_still_updates=True,
        _player_stack=Mock(currentWidget=Mock(return_value=video_surface)),
        _video_area=video_surface,
        _pending_still=None,
        _apply_still_frame=Mock(),
        _image_viewer=Mock(),
        imageLoadingFailed=Mock(),
    )
    image = QImage(8, 8, QImage.Format.Format_RGBA8888)

    PlayerViewController._on_adjusted_image_ready(controller, source, image, {})
    assert controller._pending_still is not None
    assert controller._pending_still[3] is transaction

    assert PlayerViewController.apply_pending_still(controller)
    call = controller._apply_still_frame.call_args
    assert call.args == (source, image, {})
    assert call.kwargs["transaction"] is transaction


def test_worker_result_from_previous_library_epoch_is_rejected_for_same_path() -> None:
    source = Path("/shared/photo.jpg")
    identity = AssetSourceIdentity.create(source, source_mtime_ns=1)
    old_token = PlaybackAsyncToken.create(
        library_epoch=1,
        asset_generation=1,
        asset_id="asset",
        source_identity=identity,
    )
    new_token = PlaybackAsyncToken.create(
        library_epoch=2,
        asset_generation=2,
        asset_id="asset",
        source_identity=identity,
    )
    controller = SimpleNamespace(
        _loading_source=source,
        _loading_started_at=None,
        _active_async_token=new_token,
        _apply_still_frame=Mock(),
    )

    PlayerViewController._on_adjusted_image_ready(
        controller,
        source,
        QImage(4, 4, QImage.Format.Format_RGBA8888),
        {},
        async_token=old_token,
    )

    controller._apply_still_frame.assert_not_called()
