from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from iPhoto import app as backend
from iPhoto.config import WORK_DIR_NAME
from iPhoto.core.pairing import pair_live
from iPhoto.utils.jsonio import read_json


def iso(ts: datetime) -> str:
    return ts.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _create_image(path: Path) -> None:
    image_module = pytest.importorskip(
        "PIL.Image", reason="Pillow is required to generate test images"
    )
    image = image_module.new("RGB", (8, 8), color="white")
    image.save(path)


def test_pairing_prefers_content_id() -> None:
    dt = iso(datetime(2024, 1, 1, 12, 0, 0))
    rows = [
        {
            "rel": "IMG_0001.HEIC",
            "mime": "image/heic",
            "dt": dt,
            "content_id": "CID1",
        },
        {
            "rel": "IMG_0001.MOV",
            "mime": "video/quicktime",
            "dt": dt,
            "content_id": "CID1",
            "dur": 1.5,
            "still_image_time": 0.1,
        },
    ]
    groups = pair_live(rows)
    assert len(groups) == 1
    group = groups[0]
    assert group.still == "IMG_0001.HEIC"
    assert group.motion == "IMG_0001.MOV"
    assert group.content_id == "CID1"


def test_pairing_handles_missing_mime() -> None:
    dt = iso(datetime(2024, 1, 1, 12, 0, 0))
    rows = [
        {
            "rel": "IMG_0002.HEIC",
            "mime": None,
            "dt": dt,
            "id": "still",
        },
        {
            "rel": "IMG_0002.MOV",
            "mime": None,
            "dt": dt,
            "id": "motion",
        },
    ]
    groups = pair_live(rows)
    assert len(groups) == 1
    group = groups[0]
    assert group.still == "IMG_0002.HEIC"
    assert group.motion == "IMG_0002.MOV"


def test_rescan_pairs_new_live_assets(tmp_path: Path) -> None:
    still = tmp_path / "IMG_5001.JPG"
    _create_image(still)

    # Initial scan without the motion component creates an empty links cache.
    backend.open_album(tmp_path)
    links_path = tmp_path / WORK_DIR_NAME / "links.json"
    initial = read_json(links_path)
    assert initial.get("live_groups") == []

    # Add the matching motion file and force a rescan to rebuild the cache.
    motion = tmp_path / "IMG_5001.MOV"
    motion.write_bytes(b"\x00")
    ts = still.stat().st_mtime
    os.utime(motion, (ts, ts))

    backend.rescan(tmp_path)
    updated = read_json(links_path)
    assert any(
        group.get("still") == "IMG_5001.JPG" and group.get("motion") == "IMG_5001.MOV"
        for group in updated.get("live_groups", [])
    )

def test_pairing_prefers_better_duration_score() -> None:
    """
    Verify that a video with an ideal duration is selected over one with a bad duration,
    even if the bad duration video has a 'better' (smaller) still_image_time.

    Scenario:
    - good.MOV: duration 3.0 (ideal), still_image_time 1.0
    - bad.MOV: duration 10.0 (bad), still_image_time 0.0

    The bug caused bad.MOV to be selected because its duration score was ignored
    when falling through to still_image_time comparison.
    """
    rows = [
        {
            "rel": "IMG_0001.HEIC",
            "mime": "image/heic",
            "content_id": "CID1",
        },
        {
            "rel": "good.MOV",
            "mime": "video/quicktime",
            "content_id": "CID1",
            "dur": 3.0,
            "still_image_time": 1.0,
        },
        {
            "rel": "bad.MOV",
            "mime": "video/quicktime",
            "content_id": "CID1",
            "dur": 10.0,
            "still_image_time": 0.0,
        },
    ]
    groups = pair_live(rows)
    assert len(groups) == 1
    group = groups[0]

    # We expect 'good.MOV' to be selected.
    assert group.motion == "good.MOV", f"Expected good.MOV but got {group.motion}"
