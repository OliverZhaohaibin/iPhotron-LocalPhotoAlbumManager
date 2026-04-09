from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for playback coordinator tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtCore", reason="QtCore not available", exc_type=ImportError)

from iPhoto.gui.coordinators.playback_coordinator import PlaybackCoordinator
from iPhoto.gui.ui.models.roles import Roles


def test_do_play_asset_keeps_rotate_enabled_for_video() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_vm = Mock()
    coordinator._asset_vm.rowCount.return_value = 1
    coordinator._asset_vm.index.return_value = object()

    source = Path("/fake/video.mp4")
    info = {"duration": 3.5}

    def _data(_idx, role):
        if role == Roles.ABS:
            return str(source)
        if role == Roles.IS_VIDEO:
            return True
        if role == Roles.IS_LIVE:
            return False
        if role == Roles.FEATURED:
            return False
        if role == Roles.INFO:
            return info
        return None

    coordinator._asset_vm.data.side_effect = _data
    coordinator._player_view = SimpleNamespace(
        show_video_surface=Mock(),
        video_area=Mock(),
        show_live_badge=Mock(),
        set_live_replay_enabled=Mock(),
    )
    coordinator._player_bar = Mock()
    coordinator._zoom_slider = Mock()
    coordinator._zoom_widget = Mock()
    coordinator._favorite_button = Mock()
    coordinator._info_button = Mock()
    coordinator._share_button = Mock()
    coordinator._edit_button = Mock()
    coordinator._rotate_button = Mock()
    coordinator._zoom_handler = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._info_panel = None
    coordinator._active_live_motion = None
    coordinator._active_live_still = None
    coordinator._is_playing = False

    with patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.load_adjustments",
        return_value={},
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.trim_is_non_default",
        return_value=False,
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.video_requires_adjusted_preview",
        return_value=False,
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.normalise_video_trim",
        return_value=(0.0, 3.5),
    ):
        PlaybackCoordinator._do_play_asset(coordinator, 0)

    coordinator._rotate_button.setEnabled.assert_called_with(True)


def test_do_play_asset_keeps_rotate_only_video_on_native_playback_surface() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._asset_vm = Mock()
    coordinator._asset_vm.rowCount.return_value = 1
    coordinator._asset_vm.index.return_value = object()

    source = Path("/fake/video.mp4")
    info = {"duration": 3.5}

    def _data(_idx, role):
        if role == Roles.ABS:
            return str(source)
        if role == Roles.IS_VIDEO:
            return True
        if role == Roles.IS_LIVE:
            return False
        if role == Roles.FEATURED:
            return False
        if role == Roles.INFO:
            return info
        return None

    coordinator._asset_vm.data.side_effect = _data
    coordinator._player_view = SimpleNamespace(
        show_video_surface=Mock(),
        video_area=Mock(),
        show_live_badge=Mock(),
        set_live_replay_enabled=Mock(),
    )
    coordinator._player_bar = Mock()
    coordinator._zoom_slider = Mock()
    coordinator._zoom_widget = Mock()
    coordinator._favorite_button = Mock()
    coordinator._info_button = Mock()
    coordinator._share_button = Mock()
    coordinator._edit_button = Mock()
    coordinator._rotate_button = Mock()
    coordinator._zoom_handler = Mock()
    coordinator._update_favorite_icon = Mock()
    coordinator._info_panel = None
    coordinator._active_live_motion = None
    coordinator._active_live_still = None
    coordinator._is_playing = False

    with patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.load_adjustments",
        return_value={"Crop_Rotate90": 3.0},
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.trim_is_non_default",
        return_value=False,
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.video_requires_adjusted_preview",
        return_value=False,
    ), patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.normalise_video_trim",
        return_value=(0.0, 3.5),
    ):
        PlaybackCoordinator._do_play_asset(coordinator, 0)

    coordinator._player_view.video_area.load_video.assert_called_once_with(
        source,
        adjustments={"Crop_Rotate90": 3.0},
        trim_range_ms=None,
        adjusted_preview=False,
    )


def test_rotate_current_asset_routes_video_rotation_through_video_area() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._media_session = Mock(current_row=Mock(return_value=0))
    coordinator._asset_vm = Mock()
    coordinator._asset_vm.index.return_value = object()
    coordinator._adjustment_committer = Mock(commit=Mock(return_value=True))

    source = Path("/fake/video.mp4")

    def _data(_idx, role):
        if role == Roles.ABS:
            return str(source)
        if role == Roles.IS_VIDEO:
            return True
        return None

    coordinator._asset_vm.data.side_effect = _data
    coordinator._player_view = SimpleNamespace(
        video_area=Mock(rotate_image_ccw=Mock(return_value={"Crop_Rotate90": 3.0})),
        image_viewer=Mock(rotate_image_ccw=Mock()),
    )

    with patch(
        "iPhoto.gui.coordinators.playback_coordinator.sidecar.load_adjustments",
        return_value={"Exposure": 0.2},
    ):
        PlaybackCoordinator.rotate_current_asset(coordinator)

    coordinator._player_view.video_area.rotate_image_ccw.assert_called_once_with()
    coordinator._player_view.image_viewer.rotate_image_ccw.assert_not_called()
    coordinator._adjustment_committer.commit.assert_called_once_with(
        source,
        {"Exposure": 0.2, "Crop_Rotate90": 3.0},
        reason="rotate",
    )


def test_adjustment_restore_is_deferred_while_edit_session_is_active() -> None:
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._pending_restore_path = None
    coordinator._pending_restore_reason = None
    coordinator._restore_detail_for_path = Mock()
    coordinator._is_edit_session_active = Mock(return_value=True)

    source = Path("/fake/video.mp4")

    PlaybackCoordinator._handle_restore_requested(coordinator, source, "edit_done")

    assert coordinator._pending_restore_path == source
    assert coordinator._pending_restore_reason == "edit_done"
    coordinator._restore_detail_for_path.assert_not_called()

    PlaybackCoordinator._handle_detail_view_shown(coordinator)

    coordinator._restore_detail_for_path.assert_called_once_with(source)


# ---------------------------------------------------------------------------
# Trim remapping tests for _on_video_duration_changed / _on_video_position_changed
# and _on_seek
# ---------------------------------------------------------------------------

def make_playback_coordinator(trim_in_ms: int = 0, trim_out_ms: int = 0) -> PlaybackCoordinator:
    """Return a partially-constructed PlaybackCoordinator with mocked dependencies."""
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._player_bar = Mock()
    coordinator._trim_in_ms = trim_in_ms
    coordinator._trim_out_ms = trim_out_ms
    video_area = Mock()
    video_area.is_edit_mode_active.return_value = False
    video_area.trim_range_ms.return_value = (trim_in_ms, trim_out_ms)
    coordinator._player_view = SimpleNamespace(video_area=video_area)
    return coordinator


def test_on_video_duration_changed_with_trim_shows_trimmed_duration() -> None:
    """When a trim range is active the player bar should show the trimmed duration."""
    coordinator = make_playback_coordinator(trim_in_ms=2000, trim_out_ms=7000)
    coordinator._player_view.video_area.trim_range_ms.return_value = (2000, 7000)

    PlaybackCoordinator._on_video_duration_changed(coordinator, 10_000)

    coordinator._player_bar.set_duration.assert_called_once_with(5000)  # 7000 - 2000


def test_on_video_duration_changed_without_trim_shows_full_duration() -> None:
    """When no trim is active (in == out == 0) the full clip duration is displayed."""
    coordinator = make_playback_coordinator(trim_in_ms=0, trim_out_ms=0)
    coordinator._player_view.video_area.trim_range_ms.return_value = (0, 0)

    PlaybackCoordinator._on_video_duration_changed(coordinator, 10_000)

    coordinator._player_bar.set_duration.assert_called_once_with(10_000)


def test_on_video_duration_changed_skipped_in_edit_mode() -> None:
    """In edit mode _on_video_duration_changed must return without touching the player bar."""
    coordinator = make_playback_coordinator()
    coordinator._player_view.video_area.is_edit_mode_active.return_value = True

    PlaybackCoordinator._on_video_duration_changed(coordinator, 10_000)

    coordinator._player_bar.set_duration.assert_not_called()


def test_on_video_position_changed_remaps_position_relative_to_trim_in() -> None:
    """Position is displayed relative to trim-in so the bar always starts at 0."""
    coordinator = make_playback_coordinator(trim_in_ms=3000, trim_out_ms=8000)

    PlaybackCoordinator._on_video_position_changed(coordinator, 5000)

    coordinator._player_bar.set_position.assert_called_once_with(2000)  # 5000 - 3000


def test_on_video_position_changed_clamps_to_zero_when_before_trim_in() -> None:
    """Positions before the trim-in point are clamped to 0 (not negative)."""
    coordinator = make_playback_coordinator(trim_in_ms=3000, trim_out_ms=8000)

    PlaybackCoordinator._on_video_position_changed(coordinator, 1000)

    coordinator._player_bar.set_position.assert_called_once_with(0)


def test_on_video_position_changed_skipped_in_edit_mode() -> None:
    """In edit mode position changes must not propagate to the player bar."""
    coordinator = make_playback_coordinator(trim_in_ms=1000, trim_out_ms=5000)
    coordinator._player_view.video_area.is_edit_mode_active.return_value = True

    PlaybackCoordinator._on_video_position_changed(coordinator, 2000)

    coordinator._player_bar.set_position.assert_not_called()


def test_on_seek_adds_trim_in_offset_before_forwarding_to_video_area() -> None:
    """Seeking from the bar's 0-based position must add the trim-in offset."""
    coordinator = make_playback_coordinator(trim_in_ms=4000, trim_out_ms=9000)

    PlaybackCoordinator._on_seek(coordinator, 1000)

    coordinator._player_view.video_area.seek.assert_called_once_with(5000)  # 1000 + 4000


# ---------------------------------------------------------------------------
# Regression tests: _refresh_info_panel must dispatch to read_video_meta for
# video assets and read_image_meta for photo assets.
# ---------------------------------------------------------------------------

def _make_info_panel_coordinator(info: dict) -> PlaybackCoordinator:
    """Return a minimal PlaybackCoordinator wired for _refresh_info_panel tests."""
    coordinator = PlaybackCoordinator.__new__(PlaybackCoordinator)
    coordinator._info_panel = Mock()
    coordinator._asset_vm = Mock()
    coordinator._asset_vm.index.return_value = object()
    coordinator._asset_vm.data.return_value = info
    return coordinator


def test_refresh_info_panel_calls_read_video_meta_for_video() -> None:
    """When is_video is True and frame_rate is missing, read_video_meta must be called."""
    info = {
        "is_video": True,
        "abs": "/videos/clip.mov",
    }
    coordinator = _make_info_panel_coordinator(info)

    with (
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.get_metadata_batch",
            return_value=[{}],
        ),
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.read_video_meta",
            return_value={"frame_rate": 30.0, "dur": 5.0},
        ) as mock_video,
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.read_image_meta",
        ) as mock_image,
    ):
        PlaybackCoordinator._refresh_info_panel(coordinator, 0)

    mock_video.assert_called_once()
    mock_image.assert_not_called()


def test_refresh_info_panel_calls_read_image_meta_for_photo() -> None:
    """When is_video is False and iso is missing, read_image_meta must be called."""
    info = {
        "is_video": False,
        "abs": "/photos/img.jpg",
    }
    coordinator = _make_info_panel_coordinator(info)

    with (
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.read_image_meta",
            return_value={"iso": 100, "f_number": 2.8},
        ) as mock_image,
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.read_video_meta",
        ) as mock_video,
    ):
        PlaybackCoordinator._refresh_info_panel(coordinator, 0)

    mock_image.assert_called_once()
    mock_video.assert_not_called()


def test_refresh_info_panel_skips_enrichment_when_frame_rate_and_lens_present() -> None:
    """No metadata read occurs when both frame_rate and lens are already populated."""
    info = {
        "is_video": True,
        "abs": "/videos/clip.mov",
        "frame_rate": 29.97,
        "lens": "iPhone 12 back camera 4.2mm f/1.6",
    }
    coordinator = _make_info_panel_coordinator(info)

    with (
        patch("iPhoto.gui.coordinators.playback_coordinator.read_video_meta") as mock_video,
        patch("iPhoto.gui.coordinators.playback_coordinator.read_image_meta") as mock_image,
    ):
        PlaybackCoordinator._refresh_info_panel(coordinator, 0)

    mock_video.assert_not_called()
    mock_image.assert_not_called()


def test_refresh_info_panel_enriches_when_frame_rate_present_but_lens_absent() -> None:
    """Enrichment must trigger when frame_rate is present but lens is missing."""
    info = {
        "is_video": True,
        "abs": "/videos/clip.mov",
        "frame_rate": 29.97,
        # no "lens" key — simulates a video scanned before lens extraction
    }
    coordinator = _make_info_panel_coordinator(info)

    with (
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.get_metadata_batch",
            return_value=[{"VideoKeys:LensModel": "iPhone 12 back camera 4.2mm f/1.6"}],
        ),
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.read_video_meta",
            return_value={"frame_rate": 29.97, "lens": "iPhone 12 back camera 4.2mm f/1.6"},
        ) as mock_video,
    ):
        PlaybackCoordinator._refresh_info_panel(coordinator, 0)

    mock_video.assert_called_once()
    call_args = coordinator._info_panel.set_asset_metadata.call_args[0][0]
    assert call_args.get("lens") == "iPhone 12 back camera 4.2mm f/1.6"


def test_refresh_info_panel_skips_enrichment_when_iso_present() -> None:
    """When iso is already populated no metadata read should occur for photos."""
    info = {
        "is_video": False,
        "abs": "/photos/img.jpg",
        "iso": 400,
    }
    coordinator = _make_info_panel_coordinator(info)

    with (
        patch("iPhoto.gui.coordinators.playback_coordinator.read_video_meta") as mock_video,
        patch("iPhoto.gui.coordinators.playback_coordinator.read_image_meta") as mock_image,
    ):
        PlaybackCoordinator._refresh_info_panel(coordinator, 0)

    mock_video.assert_not_called()
    mock_image.assert_not_called()


def test_refresh_info_panel_merges_enrichment_into_info_passed_to_panel() -> None:
    """Enriched metadata keys must be present when set_asset_metadata is called."""
    info = {
        "is_video": True,
        "abs": "/videos/clip.mov",
    }
    coordinator = _make_info_panel_coordinator(info)

    with (
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.get_metadata_batch",
            return_value=[{}],
        ),
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.read_video_meta",
            return_value={"frame_rate": 60.0, "dur": 3.5, "codec": "hevc"},
        ),
    ):
        PlaybackCoordinator._refresh_info_panel(coordinator, 0)

    call_args = coordinator._info_panel.set_asset_metadata.call_args[0][0]
    assert call_args.get("frame_rate") == 60.0
    assert call_args.get("codec") == "hevc"


def test_refresh_info_panel_exception_does_not_propagate() -> None:
    """A failure inside read_video_meta must be swallowed; set_asset_metadata still called."""
    info = {
        "is_video": True,
        "abs": "/videos/clip.mov",
    }
    coordinator = _make_info_panel_coordinator(info)

    with (
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.get_metadata_batch",
            return_value=[{}],
        ),
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.read_video_meta",
            side_effect=OSError("ffprobe missing"),
        ),
    ):
        # Must not raise
        PlaybackCoordinator._refresh_info_panel(coordinator, 0)

    coordinator._info_panel.set_asset_metadata.assert_called_once()


def test_refresh_info_panel_does_not_overwrite_existing_lens_with_none() -> None:
    """Enrichment must not overwrite an existing lens value with None.

    Scenario: the DB has ``lens`` from an earlier ExifTool scan, but the fresh
    ``read_video_meta`` call (e.g. ffprobe-only) returns ``lens=None``.  The
    existing value must be preserved.
    """
    info = {
        "is_video": True,
        "abs": "/videos/clip.mov",
        "lens": "iPhone 12 back camera 4.2mm f/1.6",
        # frame_rate absent to trigger enrichment
    }
    coordinator = _make_info_panel_coordinator(info)

    with (
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.get_metadata_batch",
            return_value=[{}],
        ),
        patch(
            "iPhoto.gui.coordinators.playback_coordinator.read_video_meta",
            # fresh call has no lens (e.g. ffprobe-only path)
            return_value={"frame_rate": 30.0, "lens": None},
        ),
    ):
        PlaybackCoordinator._refresh_info_panel(coordinator, 0)

    call_args = coordinator._info_panel.set_asset_metadata.call_args[0][0]
    assert call_args.get("lens") == "iPhone 12 back camera 4.2mm f/1.6"
