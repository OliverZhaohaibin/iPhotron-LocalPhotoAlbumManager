"""Tests for the video sidebar preview worker."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage

from iPhoto.gui.ui.tasks.video_sidebar_preview_worker import VideoSidebarPreviewWorker


def test_worker_extracts_preview_and_stats_off_main_flow(mocker) -> None:
    worker = VideoSidebarPreviewWorker(
        Path("/tmp/fake.mp4"),
        generation=7,
        target_size=QImage(120, 80, QImage.Format.Format_ARGB32).size(),
        still_image_time=1.5,
        duration=4.0,
        trim_in_sec=0.5,
        trim_out_sec=3.5,
    )

    image = QImage(120, 80, QImage.Format.Format_ARGB32)
    image.fill(0xFF102030)
    mocker.patch(
        "iPhoto.gui.ui.tasks.video_sidebar_preview_worker.grab_video_frame",
        return_value=image,
    )
    stats = mocker.Mock()
    compute_stats = mocker.patch(
        "iPhoto.gui.ui.tasks.video_sidebar_preview_worker.compute_color_statistics",
        return_value=stats,
    )

    ready_spy = mocker.Mock()
    worker.signals.ready.connect(ready_spy)

    worker.run()

    compute_stats.assert_called_once()
    ready_spy.assert_called_once()
    result, generation = ready_spy.call_args[0]
    assert generation == 7
    assert result.image.size() == image.size()
    assert result.stats is stats


def test_worker_reports_error_for_empty_preview(mocker) -> None:
    worker = VideoSidebarPreviewWorker(
        Path("/tmp/fake.mp4"),
        generation=3,
        target_size=QImage(120, 80, QImage.Format.Format_ARGB32).size(),
        still_image_time=None,
        duration=None,
        trim_in_sec=None,
        trim_out_sec=None,
    )

    mocker.patch(
        "iPhoto.gui.ui.tasks.video_sidebar_preview_worker.grab_video_frame",
        return_value=QImage(),
    )
    error_spy = mocker.Mock()
    worker.signals.error.connect(error_spy)

    worker.run()

    error_spy.assert_called_once()
    generation, message = error_spy.call_args[0]
    assert generation == 3
    assert "empty" in message.lower()
