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


def test_video_requires_adjusted_preview_ignores_rotate_only() -> None:
    assert sidecar.video_requires_adjusted_preview({"Crop_Rotate90": 3.0}) is False


def test_video_requires_adjusted_preview_still_flags_other_edits() -> None:
    assert sidecar.video_requires_adjusted_preview(
        {"Crop_Rotate90": 3.0, "Exposure": 0.25}
    ) is True
