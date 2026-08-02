from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests")

from iPhoto.gui.detail_pipeline import AssetSourceIdentity, PlaybackAsyncToken
from iPhoto.gui.ui.controllers import player_view_controller as player_module
from iPhoto.gui.ui.controllers.player_view_controller import (
    PlayerViewController,
    _PreparedRequestIntent,
)


def _controller(*, activate_resident: bool = False) -> SimpleNamespace:
    video_area = object()
    image_viewer = SimpleNamespace(
        activate_resident_surface=Mock(return_value=activate_resident),
    )
    return SimpleNamespace(
        _viewport_metrics=Mock(return_value=((1512, 982), 2.0)),
        _texture_limit=Mock(return_value=8192),
        _active_transaction=None,
        _current_decode_level=None,
        _active_asset_id="",
        _active_source_identity=None,
        _active_adjustments={},
        _request_reason_by_generation={},
        _session_for_request=Mock(return_value=None),
        _defer_still_updates=False,
        _player_stack=SimpleNamespace(currentWidget=Mock(return_value=image_viewer)),
        _video_area=video_area,
        _image_viewer=image_viewer,
        _still_scheduler=SimpleNamespace(
            request=Mock(return_value=True),
            prefetch=Mock(return_value=True),
        ),
        _loading_started_at=1.0,
        _loading_source=Path("/benchmark/photo.jpg"),
        _present_started_at=None,
        _present_source=None,
        _pending_present_session=None,
        show_image_surface=Mock(),
    )


def test_display_image_routes_identity_generation_and_token_to_detail_preparation() -> None:
    identity = AssetSourceIdentity.create(
        Path("/benchmark/photo.jpg"),
        size_bytes=10,
        source_mtime_ns=20,
        width=8000,
        height=6000,
    )
    token = PlaybackAsyncToken.create(
        library_epoch=3,
        asset_generation=9,
        asset_id="asset-9",
        source_identity=identity,
    )
    controller = SimpleNamespace(
        _request_generation=0,
        _active_transaction=None,
        _active_async_token=None,
        _async_token_by_generation={},
        _residency_window_generation=0,
        _loading_source=None,
        _loading_started_at=None,
        _viewport_metrics=Mock(return_value=((1512, 982), 2.0)),
        show_placeholder=Mock(),
        _schedule_adjustment_preparation=Mock(return_value=True),
    )

    assert PlayerViewController.display_image(
        controller,
        identity.path,
        asset_id="asset-9",
        request_generation=9,
        source_identity=identity,
        async_token=token,
    )

    intent = controller._schedule_adjustment_preparation.call_args.args[0]
    assert intent.asset_id == "asset-9"
    assert intent.generation == 9
    assert intent.source_identity == identity
    assert controller._active_async_token == token
    assert controller._async_token_by_generation == {9: token}
@pytest.mark.parametrize("dimensions", [(8000, 6000), (9504, 6336)])
def test_display_dispatch_is_viewport_aware_and_avoids_full_initial_decode(
    dimensions: tuple[int, int],
) -> None:
    source = Path("/benchmark/large.jpg")
    identity = AssetSourceIdentity.create(
        source,
        size_bytes=1,
        source_mtime_ns=2,
        width=dimensions[0],
        height=dimensions[1],
    )
    controller = _controller()
    intent = _PreparedRequestIntent(
        asset_id="large",
        source_identity=identity,
        generation=9,
        reason="initial",
    )

    assert PlayerViewController._dispatch_prepared_intent(controller, intent, {})

    request = controller._still_scheduler.request.call_args.args[0]
    assert request.viewport_physical_size == (1512, 982)
    assert request.device_pixel_ratio == 2.0
    assert request.decode_level != "full"
    assert max(int(request.decode_level), 0) < max(dimensions)


def test_gpu_resident_hot_return_skips_decode_and_upload_path() -> None:
    source = Path("/benchmark/a.jpg")
    identity = AssetSourceIdentity.create(
        source,
        size_bytes=1,
        source_mtime_ns=2,
        width=6000,
        height=4000,
    )
    controller = _controller(activate_resident=True)
    intent = _PreparedRequestIntent(
        asset_id="a",
        source_identity=identity,
        generation=12,
        reason="initial",
    )

    assert PlayerViewController._dispatch_prepared_intent(controller, intent, {})

    controller._image_viewer.activate_resident_surface.assert_called_once()
    controller._still_scheduler.request.assert_not_called()
    controller.show_image_surface.assert_called_once_with()


def test_delivery_requires_latest_generation_and_async_token() -> None:
    identity = AssetSourceIdentity.create(
        Path("/benchmark/a.jpg"),
        size_bytes=1,
        source_mtime_ns=2,
    )
    current = PlaybackAsyncToken.create(
        library_epoch=4,
        asset_generation=20,
        asset_id="a",
        source_identity=identity,
    )
    stale_library = PlaybackAsyncToken.create(
        library_epoch=3,
        asset_generation=20,
        asset_id="a",
        source_identity=identity,
    )
    controller = SimpleNamespace(
        _request_generation=20,
        _active_async_token=current,
        _async_token_by_generation={20: stale_library},
    )

    assert not PlayerViewController._is_generation_current(controller, 19)
    assert not PlayerViewController._is_generation_current(controller, 20)
    controller._async_token_by_generation[20] = current
    assert PlayerViewController._is_generation_current(controller, 20)


def test_production_controller_has_no_legacy_adjusted_image_worker() -> None:
    assert not hasattr(player_module, "_AdjustedImageWorker")
