"""Tests for the video trim thumbnail worker."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtGui", reason="Qt GUI not available", exc_type=ImportError)

from pathlib import Path

from PySide6.QtGui import QImage

from iPhoto.gui.ui.tasks.video_trim_thumbnail_worker import VideoTrimThumbnailWorker


def test_sample_times_use_segment_midpoints() -> None:
    worker = VideoTrimThumbnailWorker(
        Path("/tmp/fake.mp4"),
        generation=5,
        duration_sec=10.0,
        target_height=72,
        target_width=96,
        count=4,
    )

    assert worker._sample_times() == [1.25, 3.75, 6.25, 8.75]


def test_worker_emits_qimages_not_qpixmaps(monkeypatch) -> None:
    worker = VideoTrimThumbnailWorker(
        Path("/tmp/fake.mp4"),
        generation=9,
        duration_sec=4.0,
        target_height=72,
        target_width=96,
        count=2,
    )

    image = QImage(96, 72, QImage.Format.Format_ARGB32)
    image.fill(0xFF112233)
    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.video_trim_thumbnail_worker.grab_video_frame",
        lambda *args, **kwargs: image,
    )

    payloads: list[tuple[list[QImage], int]] = []
    worker.signals.ready.connect(lambda payload, generation: payloads.append((payload, generation)))

    worker.run()

    assert len(payloads) == 1
    payload, generation = payloads[0]
    assert generation == 9
    assert payload
    assert all(isinstance(item, QImage) for item in payload)


def test_worker_probes_duration_when_qt_duration_is_missing(monkeypatch) -> None:
    worker = VideoTrimThumbnailWorker(
        Path("/tmp/fake.mp4"),
        generation=13,
        duration_sec=None,
        target_height=72,
        target_width=96,
        count=2,
    )

    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.video_trim_thumbnail_worker.probe_media",
        lambda _path: {
            "format": {"duration": "4.0"},
            "streams": [{"codec_type": "video", "duration": "4.0"}],
        },
    )

    assert worker._resolved_duration_sec() == 4.0
    assert worker._sample_times() == [1.0, 3.0]


def test_contact_sheet_size_preserves_video_aspect_ratio(monkeypatch) -> None:
    worker = VideoTrimThumbnailWorker(
        Path("/tmp/fake.mp4"),
        generation=11,
        duration_sec=4.0,
        target_height=72,
        target_width=96,
        count=6,
    )

    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.video_trim_thumbnail_worker.probe_video_rotation",
        lambda _path: (0, 1920, 1080),
    )

    assert worker._contact_sheet_size() == (96, 54)


def test_worker_falls_back_to_individual_frames_when_contact_sheet_is_empty(monkeypatch) -> None:
    worker = VideoTrimThumbnailWorker(
        Path("/tmp/fake.mp4"),
        generation=17,
        duration_sec=4.0,
        target_height=72,
        target_width=96,
        count=2,
    )

    monkeypatch.setattr(worker, "_extract_contact_sheet", lambda _duration: [])
    monkeypatch.setattr(worker, "_extract_single_pass_pipe", lambda _duration: [])
    image = QImage(96, 72, QImage.Format.Format_ARGB32)
    image.fill(0xFF445566)
    calls: list[tuple[object, ...]] = []

    def _fake_grabber(*args, **kwargs):
        calls.append(args)
        return image

    monkeypatch.setattr(
        "iPhoto.gui.ui.tasks.video_trim_thumbnail_worker.grab_video_frame",
        _fake_grabber,
    )
    ready_payloads: list[tuple[list[QImage], int]] = []
    thumb_payloads: list[tuple[QImage, int]] = []
    finished_calls: list[int] = []
    worker.signals.ready.connect(lambda payload, generation: ready_payloads.append((payload, generation)))
    worker.signals.thumbnail.connect(lambda frame, generation: thumb_payloads.append((frame, generation)))
    worker.signals.finished.connect(lambda generation: finished_calls.append(generation))

    worker.run()

    assert len(calls) == 2
    assert len(ready_payloads) == 1
    assert len(thumb_payloads) == 2
    assert ready_payloads[0][1] == 17
    assert all(generation == 17 for _frame, generation in thumb_payloads)
    assert finished_calls == [17]
