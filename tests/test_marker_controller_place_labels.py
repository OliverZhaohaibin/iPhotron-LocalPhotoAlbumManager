from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for marker controller tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtCore", reason="QtCore is required for marker controller tests", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication, QObject

from iPhoto.gui.ui.widgets.marker_controller import MarkerController
from maps.map_widget.map_renderer import CityAnnotation


@pytest.fixture
def qapp() -> QCoreApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


class _DummyMapWidget:
    def __init__(self, zoom: float = 6.0) -> None:
        self.zoom = zoom


class _DummyThumbnailLoader(QObject):
    def reset_for_album(self, root: Path) -> None:
        return None

    def request(self, *args, **kwargs):
        return None


def test_marker_controller_suppresses_city_labels_when_backend_provides_them(
    qapp: QCoreApplication,
) -> None:
    controller = MarkerController(
        _DummyMapWidget(),
        _DummyThumbnailLoader(),
        marker_size=72,
        thumbnail_size=192,
        provides_place_labels=True,
    )
    emitted: list[list[CityAnnotation]] = []
    controller.citiesUpdated.connect(lambda cities: emitted.append(list(cities)))
    controller._city_annotations = [
        CityAnnotation(
            longitude=2.3522,
            latitude=48.8566,
            display_name="Paris",
            full_name="Paris, France",
        )
    ]

    try:
        controller._update_city_annotations_for_clusters([])
        qapp.processEvents()
    finally:
        controller.shutdown()

    assert controller._city_annotations == []
    assert emitted == [[]]
