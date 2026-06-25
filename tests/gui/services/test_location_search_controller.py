from __future__ import annotations

from pathlib import Path
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", exc_type=ImportError)

from PySide6.QtWidgets import QApplication

from iPhoto.gui.services.location_search_controller import LocationSearchController


@pytest.fixture()
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_location_search_controller_discards_stale_token_results(
    qapp: QApplication,
) -> None:
    del qapp
    controller = LocationSearchController()
    target_path = Path("/fake/photo.jpg")
    emitted: list[tuple[int, object, str, object]] = []
    controller.suggestionsReady.connect(lambda *args: emitted.append(args))
    controller._token = 2
    controller._target_path = target_path

    controller._handle_ready(
        1,
        target_path,
        "old",
        [SimpleNamespace(display_name="Old", secondary_text="")],
    )
    controller._handle_ready(
        2,
        target_path,
        "new",
        [SimpleNamespace(display_name="New", secondary_text="")],
    )

    assert len(emitted) == 1
    assert emitted[0][0] == 2
    assert emitted[0][2] == "new"
    controller.shutdown()
