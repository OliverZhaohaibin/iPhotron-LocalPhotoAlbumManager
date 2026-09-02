"""Real native frames and marker rendering; run outside conftest's GL mocks."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from iPhoto.application.dtos import GeotaggedAsset
from iPhoto.gui.ui.widgets import photo_map_view
from maps.map_sources import MapSourceSpec
from maps.map_widget.native_osmand_widget import NativeOsmAndWidget


class NullThumbnails(QObject):
    ready = Signal(object, str, QPixmap)

    def request(self, *args, **kwargs):
        return None


def wait_for(predicate, label, timeout=12):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, label
        QTest.qWait(20)


def main():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    fmt = QSurfaceFormat()
    fmt.setVersion(2, 1)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    photo_map_view.ThumbnailLoader = NullThumbnails
    view = photo_map_view.PhotoMapView(map_source=MapSourceSpec.osmand_default())
    view.resize(800, 600)
    view.show()
    widget = view.map_widget()
    assert isinstance(widget, NativeOsmAndWidget), view.runtime_diagnostics()
    target = widget.event_target()
    controller = view._marker_controller
    results = []
    try:
        wait_for(lambda: widget._first_frame_presented, "no native frame")
        for zoom, lon, lat, axis, direction, count in [
            (2.0, 0.0, 55.0, "y", 1, 1),
            (2.0, 0.0, -55.0, "y", -1, 1),
            (2.25, 0.0, 0.0, "y", 1, 1),
            (5.0, 0.0, 84.0, "y", 1, 1),
            (2.0, 179.0, 0.0, "x", -1, 1),
            (2.0, 0.0, 55.0, "y", 1, 2),
        ]:
            controller.clear()
            widget.set_zoom(zoom)
            widget.center_on(lon, lat)
            QTest.qWait(180)
            anchor_lon, anchor_lat = widget.center_lonlat()
            assets = [
                GeotaggedAsset(
                    library_relative=f"probe-{i}.jpg",
                    album_relative=f"probe-{i}.jpg",
                    absolute_path=Path(f"/tmp/native-pan-probe-{i}.jpg"),
                    longitude=anchor_lon + i * 5,
                    latitude=anchor_lat,
                    album_path=Path("/tmp"),
                    asset_id=str(i),
                    is_image=True,
                    is_video=False,
                    still_image_time=None,
                    duration=None,
                    location_name=None,
                    live_photo_group_id=None,
                    live_partner_rel=None,
                )
                for i in range(count)
            ]
            controller._assets = assets
            controller._rebuild_photo_clusters()
            assert len(controller._clusters) == 1
            cluster = controller._clusters[0]
            initial_offset = QPointF(cluster.projection_offset)
            start = QPoint(400, 180 if direction == 1 else 420)
            QTest.mousePress(
                target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
            )
            errors = []
            for distance in [30, 70, 100, 150, 200, 250, 300, 280]:
                movement = (
                    QPoint(0, direction * distance)
                    if axis == "y"
                    else QPoint(direction * distance, 0)
                )
                QTest.mouseMove(target, start + movement, 5)
                QTest.qWait(50)
                projected = widget.project_lonlat_exact(assets[0].longitude, assets[0].latitude)
                if projected is None:
                    assert not cluster.projection_visible, (zoom, lat, distance)
                    assert cluster.bounding_rect.isEmpty()
                    continue
                expected = projected + initial_offset
                error = max(
                    abs(cluster.screen_pos.x() - expected.x()),
                    abs(cluster.screen_pos.y() - expected.y()),
                )
                errors.append(error)
                assert error <= 1.0, (
                    zoom,
                    lat,
                    axis,
                    distance,
                    error,
                    cluster.screen_pos,
                    expected,
                )
                assert controller.cluster_at(cluster.bounding_rect.center()) is cluster
            before = QPointF(cluster.screen_pos)
            assert cluster.projection_visible, ("final anchor must be back in view", zoom, lat)
            QTest.mouseRelease(
                target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + movement
            )
            QTest.qWait(180)
            assert not controller._is_panning
            assert len(controller._clusters) == 1
            after = controller._clusters[0].screen_pos
            snap = max(abs(before.x() - after.x()), abs(before.y() - after.y()))
            assert snap <= 1.0, ("release snap", snap)
            results.append(
                {
                    "zoom": zoom,
                    "lat": lat,
                    "axis": axis,
                    "photos": count,
                    "max_error": max(errors),
                    "release_snap": snap,
                }
            )
        # Release immediately after a burst, before the historical binary's
        # mouse-drag coalescing timer has had a chance to apply every input.
        start = QPoint(100, 200)
        QTest.mousePress(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        for distance in [5, 10, 20, 40]:
            QTest.mouseMove(target, start - QPoint(0, distance), 1)
        QTest.mouseRelease(
            target,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start - QPoint(0, 40),
        )
        QTest.qWait(180)
        assert not controller._is_panning
        cluster = controller._clusters[0]
        projected = widget.project_lonlat_exact(assets[0].longitude, assets[0].latitude)
        assert projected is not None
        expected = projected + initial_offset
        rapid_error = max(
            abs(cluster.screen_pos.x() - expected.x()), abs(cluster.screen_pos.y() - expected.y())
        )
        assert rapid_error <= 1.0, ("coalesced release", rapid_error)
        print(
            "PAN_RESULT="
            + json.dumps(
                {
                    "library": str(widget.loaded_library_path()),
                    "dpr": widget.devicePixelRatioF(),
                    "surface": widget.native_surface_kind(),
                    "cases": results,
                    "rapid_release_error": rapid_error,
                }
            ),
            flush=True,
        )
    finally:
        view.shutdown()
        view.close()
        view.deleteLater()
        QTimer.singleShot(100, app.quit)
        app.exec()


if __name__ == "__main__":
    main()
