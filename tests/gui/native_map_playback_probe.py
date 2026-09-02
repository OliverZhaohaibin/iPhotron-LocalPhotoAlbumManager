"""Real macOS GPU probe, executed in a child process without conftest's GL mocks."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QTimer, Signal, qInstallMessageHandler
from PySide6.QtGui import QColor, QImage, QSurfaceFormat, QWindow
from PySide6.QtMultimedia import QVideoFrame
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from iPhoto.gui.main import _map_gl_surface_format
from iPhoto.gui.ui.main_window import MainWindow
from maps.map_widget.native_osmand_widget import NativeOsmAndWidget


class EmptyLibrary(QObject):
    """Only library IO is replaced; all window, page and GPU objects are real."""

    treeUpdated = Signal()

    def root(self):
        return None

    def list_albums(self):
        return []


def wait_for(predicate, message: str, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, message
        QTest.qWait(20)


def main(order: str) -> None:
    from types import SimpleNamespace

    warnings = []
    qInstallMessageHandler(
        lambda _kind, _context, message: warnings.append(message)
        if "No QRhi" in message or "graphics API for composition" in message else None
    )
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    QSurfaceFormat.setDefaultFormat(_map_gl_surface_format())
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    library = EmptyLibrary()
    window = MainWindow(SimpleNamespace(library=library, translation=None))
    ui = window.ui
    detail = ui._prepared_detail_page
    viewer = detail.image_viewer
    video = detail.video_area.renderer
    counters = {surface: {"submitted": 0, "failed": 0, "video": 0} for surface in (viewer, video)}
    for surface, counts in counters.items():
        surface.frameSubmitted.connect(lambda c=counts: c.update(submitted=c["submitted"] + 1))
        surface.renderFailed.connect(lambda c=counts: c.update(failed=c["failed"] + 1))
        surface.videoFramePresented.connect(lambda _g, _s, c=counts: c.update(video=c["video"] + 1))

    snapshots = []
    panel = None
    preview = None
    map_widget = None
    image = QImage(320, 240, QImage.Format.Format_RGBA8888)
    image.fill(QColor("#e04030"))
    frame = QVideoFrame(image)
    assert frame.isValid()

    def check_media(surface, previous, label):
        wait_for(lambda: counters[surface]["submitted"] > previous, f"{label}: no submitted frame")
        assert counters[surface]["failed"] == 0, (label, warnings)
        assert surface.rhi() is not None, label
        backend = surface.rhi().backend().name
        assert backend == "Metal", (label, backend)
        # Check a rendered image only after window submission. Calling grab on
        # an uninitialized widget could create a separate offscreen QRhi.
        rendered = surface.grabFramebuffer()
        assert not rendered.isNull(), label
        color = rendered.pixelColor(rendered.width() // 2, rendered.height() // 2)
        assert color.red() > 120 and color.red() > color.green() * 1.5, (label, color.name())
        snapshots.append({"step": label, "backend": backend, "center": color.name()})
        assert not warnings, warnings

    def still(label):
        ui.ensure_feature("detail")
        previous = counters[viewer]["submitted"]
        viewer.set_image(image, image_source=label)
        detail.player_stack.setCurrentWidget(viewer)
        ui.view_stack.setCurrentWidget(detail)
        check_media(viewer, previous, label)
        detail.hide_rhi_init_cover()

    def location():
        nonlocal map_widget
        page = ui.ensure_feature("map")
        ui.view_stack.setCurrentWidget(page)
        map_widget = ui.map_view.map_widget()
        assert isinstance(map_widget, NativeOsmAndWidget), ui.map_view.runtime_diagnostics()
        assert map_widget.native_surface_kind() == "opengl_window"
        assert not map_widget._native_widget.inherits("QOpenGLWidget")
        target = map_widget.event_target()
        assert isinstance(target, QWindow) and target.inherits("QOpenGLWindow")
        wait_for(lambda: map_widget._first_frame_presented, "Location did not render a map frame")
        wait_for(lambda: map_widget._native_widget.isVisible(), "Native map host stayed hidden")
        # Verify native input reaches the map and camera projection still works.
        map_widget.center_on(11.57, 48.14)
        map_widget.set_zoom(6.0)
        QTest.qWait(150)
        before = map_widget.center_lonlat()
        start = QPoint(target.width() // 2, target.height() // 2)
        QTest.mousePress(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(target, start + QPoint(35, 15), 30)
        QTest.mouseRelease(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(35, 15))
        wait_for(lambda: map_widget.center_lonlat() != before, "Native map drag did not pan")
        point = map_widget.project_lonlat(*map_widget.center_lonlat())
        assert isinstance(point, QPointF)
        snapshots.append({"step": "location", "surface": map_widget.native_surface_kind(), "map_frame": True})
        ui.view_stack.setCurrentWidget(ui.gallery_page)

    window.show()
    QTest.qWait(150)
    try:
        if order == "playback-first":
            still("before-location")
        for cycle in range(3):
            location()
            still(f"still-{cycle}")

            # Live Photo's still -> motion -> still surface transition.
            previous = counters[viewer]["submitted"]
            previous_video = counters[viewer]["video"]
            viewer.set_video_frame(frame, content_generation=cycle + 1, content_serial=cycle + 1)
            wait_for(lambda: counters[viewer]["video"] > previous_video, "Live motion frame not submitted")
            check_media(viewer, previous, f"live-motion-{cycle}")
            still(f"live-return-{cycle}")

            # Dedicated video playback uses a different QRhiWidget in VideoArea.
            previous = counters[video]["submitted"]
            previous_video = counters[video]["video"]
            detail.player_stack.setCurrentWidget(detail.video_area)
            video.update_frame(frame, content_generation=cycle + 1, content_serial=cycle + 1)
            wait_for(lambda: counters[video]["video"] > previous_video, "Video frame not submitted")
            check_media(video, previous, f"video-{cycle}")

        still("before-info")
        panel = ui.ensure_info_panel()
        panel.set_location_capability(enabled=True)
        panel.set_asset_metadata({"rel": "gpu-probe.jpg", "gps": {"lat": 48.14, "lon": 11.57}})
        panel.prepare_for_presentation()
        panel.show()
        mini = panel._location_map
        wait_for(lambda: mini.map_widget() is not None and mini.map_widget()._first_frame_presented, "Info mini-map did not render")
        wait_for(lambda: mini._placeholder.isHidden(), "Info placeholder did not clear")
        assert mini.map_widget().native_surface_kind() == "opengl_window"
        panel.dismiss()
        still("after-info")
        # The standalone preview is another consumer of the same bridge.
        from maps.main import MainWindow as MapPreviewWindow
        from maps.map_sources import MapSourceSpec

        preview = MapPreviewWindow(
            map_source=MapSourceSpec.osmand_default(),
            native_widget_class=NativeOsmAndWidget,
        )
        preview.show()
        preview_map = preview._map_widget
        assert isinstance(preview_map, NativeOsmAndWidget)
        wait_for(lambda: preview_map._first_frame_presented, "Map preview did not render")
        preview_map.shutdown()
        preview.close()
        preview.deleteLater()
        preview = None
        window.raise_()
        still("after-map-preview")
        window.resize(1000, 700)
        location()
        still("after-resize")
        assert not warnings, warnings
        print("GPU_RESULT=" + json.dumps({"order": order, "snapshots": snapshots, "warnings": warnings}), flush=True)
    finally:
        if preview is not None:
            preview._map_widget.shutdown()
            preview.close()
            preview.deleteLater()
        if panel is not None:
            panel.shutdown()
            panel.close()
        if map_widget is not None:
            ui.map_view.shutdown()
        if detail._feature_completed:
            detail.video_area.stop()
        viewer.shutdown()
        detail.video_area.edit_viewer.shutdown()
        window.close()
        window.deleteLater()
        # processEvents() alone does not drain DeferredDelete. Run the actual
        # event loop so detached mini-map containers die before QApplication.
        QTimer.singleShot(100, app.quit)
        app.exec()


if __name__ == "__main__":
    main(sys.argv[1])
