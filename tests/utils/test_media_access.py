# ruff: noqa: S101

from __future__ import annotations

import threading
from pathlib import Path

from iPhoto.utils.media_access import MediaAccessCoordinator


def test_media_access_write_waits_for_active_read(tmp_path: Path) -> None:
    path = tmp_path / "asset.mov"
    path.write_bytes(b"video")
    coordinator = MediaAccessCoordinator()
    reader_entered = threading.Event()
    release_reader = threading.Event()
    writer_entered = threading.Event()

    def reader() -> None:
        with coordinator.read(path):
            reader_entered.set()
            release_reader.wait(timeout=2)

    def writer() -> None:
        reader_entered.wait(timeout=2)
        with coordinator.write(path):
            writer_entered.set()

    reader_thread = threading.Thread(target=reader)
    writer_thread = threading.Thread(target=writer)
    reader_thread.start()
    writer_thread.start()

    assert reader_entered.wait(timeout=1)
    assert not writer_entered.wait(timeout=0.05)
    release_reader.set()
    assert writer_entered.wait(timeout=1)
    reader_thread.join(timeout=1)
    writer_thread.join(timeout=1)


def test_media_access_read_waits_for_active_write(tmp_path: Path) -> None:
    path = tmp_path / "asset.mov"
    path.write_bytes(b"video")
    coordinator = MediaAccessCoordinator()
    writer_entered = threading.Event()
    release_writer = threading.Event()
    reader_entered = threading.Event()

    def writer() -> None:
        with coordinator.write(path):
            writer_entered.set()
            release_writer.wait(timeout=2)

    def reader() -> None:
        writer_entered.wait(timeout=2)
        with coordinator.read(path):
            reader_entered.set()

    writer_thread = threading.Thread(target=writer)
    reader_thread = threading.Thread(target=reader)
    writer_thread.start()
    reader_thread.start()

    assert writer_entered.wait(timeout=1)
    assert not reader_entered.wait(timeout=0.05)
    release_writer.set()
    assert reader_entered.wait(timeout=1)
    writer_thread.join(timeout=1)
    reader_thread.join(timeout=1)
