"""Tests for the video trim thumbnail worker."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage

from iPhoto.gui.ui.tasks.video_trim_thumbnail_worker import VideoTrimThumbnailWorker


def test_sample_times_use_segment_midpoints() -> None:
    worker = VideoTrimThumbnailWorker(
        Path("/tmp/fake.mp4"),
        duration_sec=10.0,
        target_height=72,
        target_width=96,
        count=4,
    )

    assert worker._sample_times() == [1.25, 3.75, 6.25, 8.75]


def test_worker_emits_qimages_not_qpixmaps(mocker) -> None:
    worker = VideoTrimThumbnailWorker(
        Path("/tmp/fake.mp4"),
        duration_sec=4.0,
        target_height=72,
        target_width=96,
        count=2,
    )

    image = QImage(96, 72, QImage.Format.Format_ARGB32)
    image.fill(0xFF112233)
    mocker.patch(
        "iPhoto.gui.ui.tasks.video_trim_thumbnail_worker.grab_video_frame",
        return_value=image,
    )

    ready_spy = mocker.Mock()
    worker.signals.ready.connect(ready_spy)

    worker.run()

    ready_spy.assert_called_once()
    payload = ready_spy.call_args[0][0]
    assert payload
    assert all(isinstance(item, QImage) for item in payload)
