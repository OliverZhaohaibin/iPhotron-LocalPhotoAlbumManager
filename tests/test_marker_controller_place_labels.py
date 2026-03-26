from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for marker controller tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtCore", reason="QtCore is required for marker controller tests", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtCore import QPointF

from iPhoto.gui.ui.widgets.marker_controller import MarkerController
from maps.map_widget.map_renderer import CityAnnotation
from iPhoto.library.manager import GeotaggedAsset


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


class _ExactProjectionMapWidget(_DummyMapWidget):
    def __init__(self, projected_points: dict[tuple[float, float], QPointF], zoom: float = 6.0) -> None:
        super().__init__(zoom=zoom)
        self._projected_points = projected_points
        self.project_calls: list[tuple[float, float]] = []

    def width(self) -> int:
        return 800

    def height(self) -> int:
        return 600

    def prefers_exact_screen_projection(self) -> bool:
        return True

    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        key = (float(lon), float(lat))
        self.project_calls.append(key)
        return self._projected_points.get(key)


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


def test_marker_controller_uses_exact_screen_projection_when_requested(
    qapp: QCoreApplication,
    tmp_path: Path,
) -> None:
    del qapp
    assets = [
        GeotaggedAsset(
            library_relative="a.jpg",
            album_relative="a.jpg",
            absolute_path=tmp_path / "a.jpg",
            album_path=tmp_path,
            asset_id="a",
            latitude=20.0,
            longitude=10.0,
            is_image=True,
            is_video=False,
            still_image_time=None,
            duration=None,
            location_name=None,
            live_photo_group_id=None,
            live_partner_rel=None,
        ),
        GeotaggedAsset(
            library_relative="b.jpg",
            album_relative="b.jpg",
            absolute_path=tmp_path / "b.jpg",
            album_path=tmp_path,
            asset_id="b",
            latitude=40.0,
            longitude=30.0,
            is_image=True,
            is_video=False,
            still_image_time=None,
            duration=None,
            location_name=None,
            live_photo_group_id=None,
            live_partner_rel=None,
        ),
    ]
    map_widget = _ExactProjectionMapWidget(
        {
            (10.0, 20.0): QPointF(120.0, 180.0),
            (30.0, 40.0): QPointF(620.0, 420.0),
        }
    )
    controller = MarkerController(
        map_widget,
        _DummyThumbnailLoader(),
        marker_size=72,
        thumbnail_size=192,
        provides_place_labels=False,
    )
    controller._assets = assets

    try:
        controller._rebuild_photo_clusters()
    finally:
        controller.shutdown()

    assert map_widget.project_calls == [(10.0, 20.0), (30.0, 40.0)]
    assert len(controller._clusters) == 2
    assert controller._clusters[0].screen_pos == QPointF(120.0, 180.0)
    assert controller._clusters[1].screen_pos == QPointF(620.0, 420.0)
