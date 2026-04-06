"""Regression tests covering video trim persistence in ``.ipo`` sidecars."""

from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from iPhoto.core.adjustment_mapping import VIDEO_TRIM_IN_KEY, VIDEO_TRIM_OUT_KEY
from iPhoto.io import sidecar


def test_video_trim_round_trip(tmp_path: Path) -> None:
    asset = tmp_path / "clip.mov"
    asset.touch()

    sidecar.save_adjustments(
        asset,
        {
            VIDEO_TRIM_IN_KEY: 1.25,
            VIDEO_TRIM_OUT_KEY: 8.5,
            "Crop_W": 0.8,
        },
    )

    loaded = sidecar.load_adjustments(asset)

    assert loaded[VIDEO_TRIM_IN_KEY] == 1.25
    assert loaded[VIDEO_TRIM_OUT_KEY] == 8.5
    assert loaded["Crop_W"] == 0.8


def test_video_trim_written_under_video_node(tmp_path: Path) -> None:
    asset = tmp_path / "clip.mov"
    asset.touch()

    sidecar.save_adjustments(
        asset,
        {
            VIDEO_TRIM_IN_KEY: 2.0,
            VIDEO_TRIM_OUT_KEY: 4.0,
        },
    )

    tree = ET.parse(sidecar.sidecar_path_for_asset(asset))
    root = tree.getroot()
    video_node = root.find("video")

    assert video_node is not None
    assert video_node.findtext("trimInSec") == "2.000000"
    assert video_node.findtext("trimOutSec") == "4.000000"


def test_normalise_video_trim_falls_back_to_full_duration() -> None:
    trim_in, trim_out = sidecar.normalise_video_trim(
        {
            VIDEO_TRIM_IN_KEY: 12.0,
            VIDEO_TRIM_OUT_KEY: 3.0,
        },
        9.0,
    )

    assert trim_in == 0.0
    assert trim_out == 9.0


def test_normalise_video_trim_zero_out_treated_as_full_duration() -> None:
    """0.0 stored as trim_out is the in-memory sentinel meaning 'full duration'.

    Old code could corrupt a sidecar by writing trimOutSec=0.000000 when only
    the in-point was set.  normalise_video_trim must treat 0.0 the same as a
    missing value so that such sidecars load correctly instead of losing the
    in-point.
    """
    # trim_out=0.0 (sentinel) with trim_in=2.0 → should give (2.0, 10.0)
    trim_in, trim_out = sidecar.normalise_video_trim(
        {VIDEO_TRIM_IN_KEY: 2.0, VIDEO_TRIM_OUT_KEY: 0.0},
        10.0,
    )
    assert trim_in == 2.0
    assert trim_out == 10.0


def test_normalise_video_trim_zero_out_no_duration() -> None:
    """Without a known duration, 0.0 sentinel trim_out falls back to trim_out_default.

    When duration is None, trim_out_default = max(trim_in, 0), so it equals
    trim_in.  The invalid-range guard then fires and the function returns
    (0.0, trim_out_default).  This is the best possible result when neither the
    out-point nor the duration is known; the important thing is that the function
    does not treat the sentinel 0.0 as a literal 0-second out-point.
    """
    trim_in, trim_out = sidecar.normalise_video_trim(
        {VIDEO_TRIM_IN_KEY: 3.0, VIDEO_TRIM_OUT_KEY: 0.0},
        None,
    )
    assert trim_in == 0.0
    assert trim_out == 3.0


def test_write_video_node_does_not_write_zero_trim_in(tmp_path: Path) -> None:
    """trimInSec=0.0 is the default and must not be written to the sidecar."""
    asset = tmp_path / "clip.mov"
    asset.touch()

    sidecar.save_adjustments(
        asset,
        {VIDEO_TRIM_IN_KEY: 0.0, VIDEO_TRIM_OUT_KEY: 5.0},
    )

    tree = ET.parse(sidecar.sidecar_path_for_asset(asset))
    video_node = tree.getroot().find("video")

    assert video_node is not None
    assert video_node.findtext("trimInSec") is None
    assert video_node.findtext("trimOutSec") == "5.000000"


def test_write_video_node_does_not_write_zero_trim_out(tmp_path: Path) -> None:
    """trimOutSec=0.0 is the sentinel for 'full duration' and must not be persisted.

    Writing the sentinel to disk caused a regression where old sidecar files with
    trimOutSec=0.000000 were loaded back incorrectly, losing the in-point.
    """
    asset = tmp_path / "clip.mov"
    asset.touch()

    sidecar.save_adjustments(
        asset,
        {VIDEO_TRIM_IN_KEY: 2.5, VIDEO_TRIM_OUT_KEY: 0.0},
    )

    tree = ET.parse(sidecar.sidecar_path_for_asset(asset))
    video_node = tree.getroot().find("video")

    # Only trimInSec should be present; the sentinel 0.0 must not be written.
    assert video_node is not None
    assert video_node.findtext("trimInSec") == "2.500000"
    assert video_node.findtext("trimOutSec") is None


def test_video_requires_adjusted_preview_ignores_rotate_only() -> None:
    assert sidecar.video_requires_adjusted_preview({"Crop_Rotate90": 3.0}) is False


def test_video_requires_adjusted_preview_still_flags_other_edits() -> None:
    assert sidecar.video_requires_adjusted_preview(
        {"Crop_Rotate90": 3.0, "Exposure": 0.25}
    ) is True
