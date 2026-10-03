"""Qt workers transport results; installation regressions live in infrastructure tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from iPhoto.application.ports.map_extension import MapExtensionError
from iPhoto.gui.ui.tasks.map_extension_download_worker import (
    MapExtensionDownloadWorker,
    MapExtensionDownloadRequest,
    MapExtensionDownloadResult,
)


def test_worker_transports_progress_result_and_finished():
    payload = MapExtensionDownloadRequest(Path("maps"), "win32")
    result = MapExtensionDownloadResult(Path("pending"), Path("active"), "pending_restart")

    def execute(request, progress):
        assert request is payload
        progress(1, 10, "Downloading map extension...")
        return result

    worker = MapExtensionDownloadWorker(payload, SimpleNamespace(execute=execute))
    events = []
    worker.signals.progress.connect(lambda *args: events.append(args))
    worker.signals.ready.connect(lambda value: events.append(value))
    worker.signals.finished.connect(lambda: events.append("finished"))
    worker.run()
    assert events == [(1, 10, "Downloading map extension..."), result, "finished"]


@pytest.mark.parametrize("known", [True, False])
def test_worker_transports_safe_failure_and_always_finishes(known):
    failure = MapExtensionError("refused", "download", code=10061)

    def execute(*args):
        raise failure if known else RuntimeError("http://user:password@proxy")

    worker = MapExtensionDownloadWorker(
        MapExtensionDownloadRequest(Path("maps"), "win32"), SimpleNamespace(execute=execute)
    )
    errors, done = [], []
    worker.signals.error.connect(errors.append)
    worker.signals.finished.connect(lambda: done.append(True))
    worker.run()
    assert errors[0] is failure if known else errors[0].category == "installation"
    assert "password" not in str(errors[0])
    assert done == [True]
