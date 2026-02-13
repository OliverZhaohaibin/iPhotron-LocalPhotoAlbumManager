
from iPhoto.core.pairing import pair_live

def test_duplicates_pair_one_to_one():
    """
    Verify that multiple photos sharing the same Content Identifier
    are paired 1:1 with unique videos, rather than all grabbing the same 'best' video.
    """
    cid = "uuid-duplicate-test"

    # Photo 1 and Video 1
    p1 = {"rel": "folder1/photo.jpg", "mime": "image/jpeg", "content_id": cid}
    v1 = {"rel": "folder1/video.mov", "mime": "video/quicktime", "content_id": cid, "dur": 3.0}

    # Photo 2 and Video 2 (Duplicates)
    p2 = {"rel": "folder2/photo.jpg", "mime": "image/jpeg", "content_id": cid}
    v2 = {"rel": "folder2/video.mov", "mime": "video/quicktime", "content_id": cid, "dur": 3.0}

    index_rows = [p1, v1, p2, v2]

    groups = pair_live(index_rows)

    # We expect 2 groups
    assert len(groups) == 2

    # Verify each group has a unique motion file
    motion_files = [g.motion for g in groups]
    assert len(set(motion_files)) == 2, f"Videos reused! {motion_files}"


def test_still_image_time_preference():
    """
    Verify that a video with a valid non-negative still_image_time is preferred
    over one with a negative (invalid) still_image_time, even if the latter is 'smaller'.
    """
    cid = "uuid-still-time-test"

    # Two videos with same duration (so duration score is equal)
    # v_invalid has -1.0 (invalid)
    # v_valid has 0.5 (valid)

    photo = {"rel": "photo.heic", "mime": "image/heic", "content_id": cid}

    v_invalid = {
        "rel": "invalid.mov",
        "mime": "video/quicktime",
        "content_id": cid,
        "dur": 3.0,
        "still_image_time": -1.0
    }

    v_valid = {
        "rel": "valid.mov",
        "mime": "video/quicktime",
        "content_id": cid,
        "dur": 3.0,
        "still_image_time": 0.5
    }

    index_rows = [photo, v_invalid, v_valid]

    groups = pair_live(index_rows)

    assert len(groups) == 1
    # Before fix, -1 < 0.5, so invalid.mov might be picked (depending on order)
    # We want valid.mov
    assert groups[0].motion == "valid.mov", f"Expected valid.mov, got {groups[0].motion}"


def test_still_image_time_none_vs_valid():
    """
    Verify that a video with a valid still_image_time is preferred
    over one with None still_image_time.
    """
    cid = "uuid-none-vs-valid-test"

    photo = {"rel": "photo.heic", "mime": "image/heic", "content_id": cid}

    # Video without still_image_time (None)
    v_none = {
        "rel": "none.mov",
        "mime": "video/quicktime",
        "content_id": cid,
        "dur": 3.0,
    }

    # Video with valid still_image_time
    v_valid = {
        "rel": "valid.mov",
        "mime": "video/quicktime",
        "content_id": cid,
        "dur": 3.0,
        "still_image_time": 0.5
    }

    # Test with v_none first in the list
    index_rows = [photo, v_none, v_valid]
    groups = pair_live(index_rows)

    assert len(groups) == 1
    assert groups[0].motion == "valid.mov", f"Expected valid.mov, got {groups[0].motion}"

    # Test with v_valid first in the list to ensure order doesn't matter
    index_rows_reversed = [photo, v_valid, v_none]
    groups_reversed = pair_live(index_rows_reversed)

    assert len(groups_reversed) == 1
    assert groups_reversed[0].motion == "valid.mov", f"Expected valid.mov, got {groups_reversed[0].motion}"
