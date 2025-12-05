from __future__ import annotations
from datetime import datetime, timezone
from iPhotos.src.iPhoto.core.pairing import pair_live

def iso(ts: datetime) -> str:
    return ts.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

def test_mp4_content_id_pairing():
    dt = iso(datetime(2024, 1, 1, 12, 0, 0))
    rows = [
        {
            "rel": "IMG_0001.HEIC",
            "mime": "image/heic",
            "dt": dt,
            "content_id": "CID1",
        },
        {
            "rel": "IMG_0001.MP4",
            "mime": "video/mp4",
            "dt": dt,
            "content_id": "CID1",
            "dur": 1.5,
        },
    ]
    groups = pair_live(rows)

    # Expectation: Should be paired because Content ID matches
    assert len(groups) == 1, "MP4 with matching Content ID was not paired"
    group = groups[0]
    assert group.still == "IMG_0001.HEIC"
    assert group.motion == "IMG_0001.MP4"


def test_heic_with_content_id_is_not_treated_as_video():
    """
    Ensure that a still image with a Content Identifier is not incorrectly
    identified as a motion component.
    """
    dt = iso(datetime(2024, 1, 1, 12, 0, 0))
    rows = [
        {
            "rel": "IMG_0002.HEIC",
            "mime": "image/heic",
            "dt": dt,
            "content_id": "CID2",
        }
    ]
    groups = pair_live(rows)

    # Expectation: No pairing should occur because there is no video component.
    # If the HEIC was incorrectly treated as a video (due to content_id),
    # it might pair with itself or cause other issues.
    assert len(groups) == 0, "Standalone HEIC with Content ID should not form a Live Group"
