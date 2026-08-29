"""Tests for the init cover management in PlayerViewController.

The init cover is an opaque widget that hides uninitialised QRhiWidget
backing textures.  It must stay visible until the currently shown
QRhiWidget has rendered its first opaque frame, and must be re-shown
when switching to a QRhiWidget that has not yet rendered.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from threading import Event
from unittest.mock import call

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for GUI tests", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", reason="Qt widgets not available", exc_type=ImportError)
pytest.importorskip("PySide6.QtMultimedia", reason="QtMultimedia is required", exc_type=ImportError)

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QWidget

from iPhoto.gui.detail_decode_backend import DecodedSurface
from iPhoto.gui.detail_pipeline import (
    AssetSourceIdentity,
    DetailDecodeKey,
    DetailGeometryState,
    DetailRenderRequest,
)
from iPhoto.gui.ui.controllers.player_view_controller import (
    PlayerViewController,
    PreparedStillState,
    _AdjustmentPreparationSignals,
    _AdjustmentPreparationWorker,
    _PreparedRequestIntent,
)
from iPhoto.gui.ui.widgets.detail_page import DetailPageWidget
from iPhoto.people.repository import AssetFaceAnnotation


def _surface(path: Path, image: QImage, *, level: int = 1024) -> DecodedSurface:
    request = DetailRenderRequest(
        generation=1,
        asset_id="asset-1",
        source_identity=AssetSourceIdentity.create(
            path,
            width=image.width(),
            height=image.height(),
            source_mtime_ns=1,
        ),
        viewport_physical_size=(800, 600),
        device_pixel_ratio=1.0,
        geometry=DetailGeometryState(),
        reason="initial",
        decode_level=level,
    )
    return DecodedSurface(
        image=image,
        decode_key=DetailDecodeKey.from_request(request),
        source_size=(image.width(), image.height()),
        decoded_size=(image.width(), image.height()),
        decode_level=level,
        backend="fake",
    )


def _spin_until(qapp, predicate, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    qapp.processEvents()
    return bool(predicate())


def _assert_ibeam_cursor() -> None:
    cursor = QApplication.overrideCursor()
    assert cursor is not None
    assert cursor.shape() == Qt.CursorShape.IBeamCursor


def _chip_margin_point(overlay, face_id: str = "face-1") -> QPointF:
    hover_rect = overlay._states[face_id].layout.hover_rect
    visual_rect = overlay._states[face_id].layout.chip_rect
    candidates = [
        QPointF(visual_rect.left() - 3.0, visual_rect.center().y()),
        QPointF(visual_rect.right() + 3.0, visual_rect.center().y()),
        QPointF(visual_rect.center().x(), visual_rect.top() - 3.0),
        QPointF(visual_rect.center().x(), visual_rect.bottom() + 3.0),
    ]
    for point in candidates:
        if hover_rect.contains(point) and not visual_rect.contains(point):
            return point
    return QPointF(hover_rect.left() + 1.0, hover_rect.center().y())


def _send_mouse_move_to_overlay_point(target: QWidget, overlay, point: QPointF) -> None:
    target_point = target.mapFromGlobal(overlay.mapToGlobal(point.toPoint()))
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(target_point),
        QPointF(target_point),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(target, move_event)


class _FakeImageViewer(QWidget):
    """Minimal stand-in for GLImageViewer used by the test harness.

    Provides only the signals and methods that ``PlayerViewController``
    connects to during construction.
    """

    firstFrameReady = Signal()
    renderResourcesInvalidated = Signal()
    stillFrameSubmitted = Signal(object, int)
    frameSubmitted = Signal()
    replayRequested = Signal()
    viewTransformChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._has_image_content = True
        self._current_source = None
        self._adjustments = {}
        self.setMouseTracking(True)

    def set_image(self, *args, **kwargs):
        self._current_source = kwargs.get("image_source")

    def current_image_source(self):
        return self._current_source

    def set_adjustments(self, adjustments):
        self._adjustments = dict(adjustments)

    def zoom_factor(self):
        return 1.0

    def set_live_replay_enabled(self, enabled):
        pass

    def update(self):
        pass

    def has_image_content(self) -> bool:
        return self._has_image_content

    def image_rect_to_viewport(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        image_width: float | None = None,
        image_height: float | None = None,
    ) -> QRectF:
        del image_width, image_height
        return QRectF(float(x), float(y), float(width), float(height))


class _FakePlayerBar(QWidget):
    """Player-bar seam required by ``DetailPageWidget.retranslate_ui``."""

    def retranslate_ui(self) -> None:
        pass


class _FakeVideoArea(QWidget):
    """Widget-only video seam for controller tests that do not exercise media IO.

    Constructing the production ``VideoArea`` starts QtMultimedia and its native
    backend.  That is outside this module's init-cover contract and can crash on
    Linux's offscreen platform before the first assertion is reached.
    """

    firstFrameReady = Signal()
    surfaceFrameSubmitted = Signal(int, int)
    surfaceInvalidated = Signal(int, int)
    surfaceCompositionSubmitted = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.player_bar = _FakePlayerBar(self)

    def hide_controls(self, *, animate: bool = True) -> None:
        del animate

    def show_controls(self, *, animate: bool = True) -> None:
        del animate

    def set_controls_enabled(self, enabled: bool) -> None:
        del enabled

    def video_view(self) -> QWidget:
        return self

    def request_active_surface_update(self) -> None:
        pass


@pytest.fixture(scope="module")
def qapp():
    """Create QApplication instance for Qt tests."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()


@pytest.fixture
def controller(qapp):
    """Build a PlayerViewController with a fake image viewer."""
    parent_widget = QWidget()
    stack = QStackedWidget(parent_widget)
    placeholder = QLabel("placeholder")
    image_viewer = _FakeImageViewer()
    video_area = _FakeVideoArea()
    from iPhoto.gui.ui.widgets.live_badge import LiveBadge

    live_badge = LiveBadge(parent_widget)
    live_badge.hide()

    stack.addWidget(placeholder)
    stack.addWidget(image_viewer)
    stack.addWidget(video_area)

    pvc = PlayerViewController(
        player_stack=stack,
        image_viewer=image_viewer,
        video_area=video_area,
        placeholder=placeholder,
        live_badge=live_badge,
    )
    try:
        yield pvc
    finally:
        pvc.shutdown(timeout_ms=500)
        parent_widget.close()
        parent_widget.deleteLater()
        qapp.processEvents()


class TestInitCoverTracking:
    """Per-widget first-render tracking in PlayerViewController."""

    def test_initial_render_flags(self, controller):
        """Both render flags should start as False."""
        assert controller._image_viewer_rendered is False
        assert controller._video_renderer_rendered is False

    def test_still_decode_uses_dedicated_bounded_pool(self, controller):
        assert controller._pool is not QThreadPool.globalInstance()
        assert controller._pool.maxThreadCount() == 2
        assert controller._preparation_pool.maxThreadCount() == 2

    def test_latest_preparation_bypasses_one_blocked_raw_worker(
        self,
        controller,
        qapp,
        tmp_path,
        mocker,
    ):
        source_a = tmp_path / "a.nef"
        source_b = tmp_path / "b.jpg"
        source_a.write_bytes(b"raw-a")
        source_b.write_bytes(b"jpeg-b")
        probe_started = Event()
        release_probe = Event()
        b_prepared = Event()
        dispatched: list[Path] = []

        def probe(identity):
            probe_started.set()
            assert release_probe.wait(5)
            return AssetSourceIdentity.create(
                identity.path,
                width=4000,
                height=3000,
            )

        class _EditService:
            def read_adjustments(self, source):
                if Path(source) == source_b.absolute():
                    b_prepared.set()
                return {}

        mocker.patch(
            "iPhoto.gui.ui.controllers.player_view_controller.probe_raw_source_identity",
            side_effect=probe,
        )
        controller._edit_service_getter = lambda: _EditService()
        mocker.patch.object(
            controller,
            "_dispatch_prepared_intent",
            side_effect=lambda intent, _adjustments: dispatched.append(
                intent.source_identity.path
            )
            or True,
        )
        raw_identity = AssetSourceIdentity.create(source_a, width=0, height=0)
        jpeg_identity = AssetSourceIdentity.create(source_b, width=1600, height=1200)

        try:
            assert controller._schedule_adjustment_preparation(
                _PreparedRequestIntent("A", raw_identity, 1, "initial")
            )
            assert probe_started.wait(5)
            first_a = next(iter(controller._preparation_entries.values())).worker

            assert controller._schedule_adjustment_preparation(
                _PreparedRequestIntent("B", jpeg_identity, 2, "initial")
            )

            assert b_prepared.wait(5)
            assert _spin_until(qapp, lambda: dispatched == [source_b.absolute()])
            assert first_a._cancelled
            assert not release_probe.is_set()
        finally:
            release_probe.set()
            assert controller._preparation_pool.waitForDone(5000)
            qapp.processEvents()

        assert dispatched == [source_b.absolute()]

    def test_neighbor_prefetch_reserves_one_lane_for_foreground(
        self,
        controller,
        qapp,
        tmp_path,
        mocker,
    ):
        sources = {
            "A": tmp_path / "a.nef",
            "B": tmp_path / "b.nef",
            "C": tmp_path / "c.jpg",
        }
        for source in sources.values():
            source.write_bytes(b"source")
        started = {name: Event() for name in sources}
        releases = {name: Event() for name in ("A", "B")}
        dispatched: list[tuple[str, Path]] = []

        def probe(identity):
            name = identity.path.stem.upper()
            started[name].set()
            assert releases[name].wait(5)
            return AssetSourceIdentity.create(identity.path, width=4000, height=3000)

        class _EditService:
            def read_adjustments(self, source):
                started[Path(source).stem.upper()].set()
                return {}

        mocker.patch(
            "iPhoto.gui.ui.controllers.player_view_controller.probe_raw_source_identity",
            side_effect=probe,
        )
        controller._edit_service_getter = lambda: _EditService()
        mocker.patch.object(
            controller,
            "_dispatch_prepared_intent",
            side_effect=lambda intent, _adjustments: dispatched.append(
                (intent.reason, intent.source_identity.path)
            )
            or True,
        )

        try:
            assert controller.prefetch_images([sources["A"], sources["B"]])
            assert started["A"].wait(5)
            assert not started["B"].is_set()
            assert len(controller._preparation_prefetch_queue) == 1

            assert controller._schedule_adjustment_preparation(
                _PreparedRequestIntent(
                    "C",
                    AssetSourceIdentity.create(
                        sources["C"], width=1600, height=1200
                    ),
                    1,
                    "initial",
                )
            )
            assert started["C"].wait(5)
            assert _spin_until(
                qapp,
                lambda: dispatched == [("initial", sources["C"].absolute())],
            )
            assert not releases["A"].is_set()
            assert not started["B"].is_set()
            assert controller._preparation_prefetch_queue == []
        finally:
            for release in releases.values():
                release.set()
            assert controller._preparation_pool.waitForDone(5000)
            qapp.processEvents()

        assert dispatched == [("initial", sources["C"].absolute())]
        assert not started["B"].is_set()

    def test_neighbor_prefetch_preparations_run_serially(
        self,
        controller,
        qapp,
        tmp_path,
        mocker,
    ):
        sources = {name: tmp_path / f"{name.lower()}.nef" for name in ("A", "B")}
        for source in sources.values():
            source.write_bytes(b"raw")
        started = {name: Event() for name in sources}
        releases = {name: Event() for name in sources}
        dispatched: list[tuple[str | None, Path]] = []

        def probe(identity):
            name = identity.path.stem.upper()
            started[name].set()
            assert releases[name].wait(5)
            return AssetSourceIdentity.create(identity.path, width=4000, height=3000)

        mocker.patch(
            "iPhoto.gui.ui.controllers.player_view_controller.probe_raw_source_identity",
            side_effect=probe,
        )
        mocker.patch.object(
            controller,
            "_dispatch_prepared_intent",
            side_effect=lambda intent, _adjustments: dispatched.append(
                (intent.residency_slot, intent.source_identity.path)
            )
            or True,
        )

        try:
            assert controller.prefetch_images([sources["A"], sources["B"]])
            assert started["A"].wait(5)
            assert not started["B"].is_set()

            releases["A"].set()
            assert _spin_until(
                qapp,
                lambda: dispatched == [("previous", sources["A"].absolute())],
            )
            assert started["B"].wait(5)

            releases["B"].set()
            assert _spin_until(
                qapp,
                lambda: dispatched
                == [
                    ("previous", sources["A"].absolute()),
                    ("next", sources["B"].absolute()),
                ],
            )
        finally:
            for release in releases.values():
                release.set()
            assert controller._preparation_pool.waitForDone(5000)
            qapp.processEvents()

        assert controller._preparation_prefetch_queue == []

    def test_queued_neighbor_click_uses_bypass_lane_without_duplicate_worker(
        self,
        controller,
        qapp,
        tmp_path,
        mocker,
    ):
        source_a = tmp_path / "a.nef"
        source_b = tmp_path / "b.jpg"
        source_a.write_bytes(b"raw")
        source_b.write_bytes(b"jpeg")
        a_started = Event()
        release_a = Event()
        b_reads = 0
        dispatched: list[tuple[str, Path]] = []

        def probe(identity):
            a_started.set()
            assert release_a.wait(5)
            return AssetSourceIdentity.create(identity.path, width=4000, height=3000)

        class _EditService:
            def read_adjustments(self, source):
                nonlocal b_reads
                if Path(source) == source_b.absolute():
                    b_reads += 1
                return {}

        mocker.patch(
            "iPhoto.gui.ui.controllers.player_view_controller.probe_raw_source_identity",
            side_effect=probe,
        )
        controller._edit_service_getter = lambda: _EditService()
        mocker.patch.object(
            controller,
            "_dispatch_prepared_intent",
            side_effect=lambda intent, _adjustments: dispatched.append(
                (intent.reason, intent.source_identity.path)
            )
            or True,
        )

        try:
            assert controller.prefetch_images([source_a, source_b])
            assert a_started.wait(5)
            assert len(controller._preparation_prefetch_queue) == 1

            assert controller._schedule_adjustment_preparation(
                _PreparedRequestIntent(
                    "",
                    AssetSourceIdentity.create(source_b, width=1600, height=1200),
                    7,
                    "initial",
                )
            )
            assert _spin_until(
                qapp,
                lambda: dispatched == [("initial", source_b.absolute())],
            )
            assert b_reads == 1
            assert controller._preparation_prefetch_queue == []
            assert not release_a.is_set()
        finally:
            release_a.set()
            assert controller._preparation_pool.waitForDone(5000)
            qapp.processEvents()

        assert b_reads == 1
        assert dispatched == [("initial", source_b.absolute())]

    def test_new_neighbor_window_replaces_queue_and_waits_for_old_terminal(
        self,
        controller,
        tmp_path,
    ):
        class _Pool:
            def __init__(self):
                self.queued = []
                self.starts = []

            def start(self, worker, priority):
                self.queued.append(worker)
                self.starts.append((worker, priority))

            def tryTake(self, worker):
                if worker not in self.queued:
                    return False
                self.queued.remove(worker)
                return True

            def mark_running(self, worker):
                self.queued.remove(worker)

        sources = [tmp_path / f"{name}.jpg" for name in ("a", "b", "c", "d")]
        for source in sources:
            source.write_bytes(b"jpeg")
        original_pool = controller._preparation_pool
        pool = _Pool()
        controller._preparation_pool = pool
        workers = []

        try:
            assert controller.prefetch_images(sources[:2])
            old_worker = next(iter(controller._preparation_entries.values())).worker
            workers.append(old_worker)
            pool.mark_running(old_worker)

            assert controller.prefetch_images(sources[2:])
            assert old_worker._cancelled
            assert controller._preparation_entries == {}
            assert [intent.source_identity.path for intent in controller._preparation_prefetch_queue] == [
                sources[2].absolute(),
                sources[3].absolute(),
            ]
            assert pool.queued == []

            controller._on_adjustment_preparation_finished(old_worker)
            new_worker = next(iter(controller._preparation_entries.values())).worker
            workers.append(new_worker)
            assert new_worker.source_identity.path == sources[2].absolute()
            assert [intent.source_identity.path for intent in controller._preparation_prefetch_queue] == [
                sources[3].absolute()
            ]

            controller.cancel_pending_image_requests()
            assert controller._preparation_prefetch_queue == []
            controller._on_adjustment_preparation_finished(new_worker)
            assert len(pool.starts) == 2
        finally:
            for worker in workers:
                controller._on_adjustment_preparation_finished(worker)
            controller._preparation_pool = original_pool

    def test_cancelled_preparation_key_is_not_reused_for_a_b_a(
        self,
        controller,
        tmp_path,
        mocker,
    ):
        class _Pool:
            def __init__(self):
                self.queued = []

            def start(self, worker, _priority):
                self.queued.append(worker)

            def tryTake(self, worker):
                if worker not in self.queued:
                    return False
                self.queued.remove(worker)
                return True

            def mark_running(self, worker):
                self.queued.remove(worker)

        original_pool = controller._preparation_pool
        pool = _Pool()
        controller._preparation_pool = pool
        dispatch = mocker.patch.object(controller, "_dispatch_prepared_intent")
        failure = mocker.patch.object(controller, "_on_adjusted_image_failed")
        identity_a = AssetSourceIdentity.create(
            tmp_path / "a.raw", width=4000, height=3000, source_mtime_ns=1
        )
        identity_b = AssetSourceIdentity.create(
            tmp_path / "b.jpg", width=1600, height=1200, source_mtime_ns=1
        )
        workers = []

        try:
            assert controller._schedule_adjustment_preparation(
                _PreparedRequestIntent("A", identity_a, 1, "initial")
            )
            first_a = next(iter(controller._preparation_entries.values())).worker
            workers.append(first_a)
            pool.mark_running(first_a)

            assert controller._schedule_adjustment_preparation(
                _PreparedRequestIntent("B", identity_b, 2, "initial")
            )
            worker_b = next(iter(controller._preparation_entries.values())).worker
            workers.append(worker_b)
            pool.mark_running(worker_b)

            assert controller._schedule_adjustment_preparation(
                _PreparedRequestIntent("A", identity_a, 3, "initial")
            )
            second_a = next(iter(controller._preparation_entries.values())).worker
            workers.append(second_a)

            assert first_a._cancelled
            assert worker_b._cancelled
            assert second_a is not first_a
            assert second_a.generation == 3
            assert controller._preparation_entries[second_a.key].worker is second_a

            stale_state = PreparedStillState.create({}, identity_a)
            controller._on_adjustment_prepared(first_a, stale_state)
            controller._on_adjustment_preparation_failed(first_a, "stale failure")
            controller._on_adjustment_preparation_finished(first_a)

            dispatch.assert_not_called()
            failure.assert_not_called()
            assert controller._preparation_entries[second_a.key].worker is second_a
        finally:
            for worker in workers:
                controller._on_adjustment_preparation_finished(worker)
            controller._preparation_pool = original_pool

    def test_latest_preparation_starts_when_one_of_two_stale_lanes_releases(
        self,
        controller,
        qapp,
        tmp_path,
        mocker,
    ):
        sources = {
            "A": tmp_path / "a.nef",
            "B": tmp_path / "b.nef",
            "C": tmp_path / "c.jpg",
        }
        for source in sources.values():
            source.write_bytes(b"source")
        started = {name: Event() for name in ("A", "B", "C")}
        releases = {name: Event() for name in ("A", "B")}
        dispatched: list[Path] = []

        def probe(identity):
            name = identity.path.stem.upper()
            started[name].set()
            assert releases[name].wait(5)
            return AssetSourceIdentity.create(identity.path, width=4000, height=3000)

        class _EditService:
            def read_adjustments(self, source):
                name = Path(source).stem.upper()
                started[name].set()
                return {}

        mocker.patch(
            "iPhoto.gui.ui.controllers.player_view_controller.probe_raw_source_identity",
            side_effect=probe,
        )
        controller._edit_service_getter = lambda: _EditService()
        mocker.patch.object(
            controller,
            "_dispatch_prepared_intent",
            side_effect=lambda intent, _adjustments: dispatched.append(
                intent.source_identity.path
            )
            or True,
        )

        try:
            for generation, name in enumerate(("A", "B"), start=1):
                assert controller._schedule_adjustment_preparation(
                    _PreparedRequestIntent(
                        name,
                        AssetSourceIdentity.create(
                            sources[name], width=0, height=0
                        ),
                        generation,
                        "initial",
                    )
                )
                assert started[name].wait(5)

            assert controller._schedule_adjustment_preparation(
                _PreparedRequestIntent(
                    "C",
                    AssetSourceIdentity.create(
                        sources["C"], width=1600, height=1200
                    ),
                    3,
                    "initial",
                )
            )
            assert not started["C"].is_set()

            releases["B"].set()
            assert started["C"].wait(5)
            assert not releases["A"].is_set()
            assert _spin_until(qapp, lambda: dispatched == [sources["C"].absolute()])
        finally:
            for release in releases.values():
                release.set()
            assert controller._preparation_pool.waitForDone(5000)
            qapp.processEvents()

        assert dispatched == [sources["C"].absolute()]

    def test_hover_adjustment_preparation_is_promoted_for_click(self, controller):
        class _Pool:
            def __init__(self):
                self.queued = []
                self.starts = []

            def start(self, worker, priority):
                self.queued.append(worker)
                self.starts.append((worker, priority))

            def tryTake(self, worker):
                if worker not in self.queued:
                    return False
                self.queued.remove(worker)
                return True

            def clear(self):
                self.queued.clear()

            def waitForDone(self, timeout_ms):
                del timeout_ms
                return True

        pool = _Pool()
        controller._preparation_pool = pool
        identity = AssetSourceIdentity.create(
            Path("/tmp/photo.jpg"),
            width=4000,
            height=3000,
            source_mtime_ns=1,
        )
        assert controller._schedule_adjustment_preparation(
            _PreparedRequestIntent("asset-1", identity, 0, "prefetch")
        )
        assert controller._schedule_adjustment_preparation(
            _PreparedRequestIntent("asset-1", identity, 7, "initial")
        )

        assert len(controller._preparation_entries) == 1
        entry = next(iter(controller._preparation_entries.values()))
        assert [intent.generation for intent in entry.intents] == [0, 7]
        assert [priority for _, priority in pool.starts] == [-1, 1]
        assert entry.worker.generation == 7

    def test_raw_preparation_repairs_unknown_geometry_before_lod_selection(
        self,
        controller,
        mocker,
    ):
        source = Path("/tmp/legacy.nef")
        unknown = AssetSourceIdentity.create(
            source,
            width=0,
            height=0,
            source_mtime_ns=1,
        )
        repaired = AssetSourceIdentity.create(
            source,
            width=4644,
            height=3084,
            source_mtime_ns=1,
        )
        mocker.patch(
            "iPhoto.gui.ui.controllers.player_view_controller.probe_raw_source_identity",
            return_value=repaired,
        )
        emit = mocker.patch(
            "iPhoto.gui.ui.controllers.player_view_controller.emit_detail_event"
        )
        signals = _AdjustmentPreparationSignals()
        states: list[PreparedStillState] = []
        signals.ready.connect(lambda _key, state: states.append(state))
        worker = _AdjustmentPreparationWorker(
            ("asset",),
            unknown,
            signals,
            None,
            generation=7,
        )

        worker.run()

        assert len(states) == 1
        assert states[0].source_identity == repaired
        assert states[0].adjustments == {}
        assert emit.call_args_list[0].kwargs["generation"] == 7

        controller._image_viewer.resize(1512, 982)
        controller._request_generation = 7
        request = mocker.patch.object(
            controller._still_scheduler,
            "request",
            return_value=True,
        )
        assert controller._dispatch_prepared_intent(
            _PreparedRequestIntent("asset", repaired, 7, "initial"),
            {},
        )
        scheduled = request.call_args.args[0]
        assert scheduled.source_identity == repaired
        assert scheduled.decode_level != "full"

    def test_stale_decode_generation_is_not_applied(self, controller, mocker):
        apply_ready = mocker.patch.object(controller, "_on_adjusted_image_ready")
        controller._request_generation = 2

        controller._on_scheduled_image_ready(
            1,
            _surface(
                Path("/tmp/stale.jpg"),
                QImage(2, 2, QImage.Format.Format_RGBA8888),
            ),
        )

        apply_ready.assert_not_called()

    def test_normalized_surface_is_uploaded_without_gui_scaling(self, controller, mocker):
        image = QImage(1024, 10, QImage.Format.Format_RGBA8888)
        set_image = mocker.patch.object(controller._image_viewer, "set_image")

        surface = _surface(Path("/tmp/huge.jpg"), image)
        controller._apply_still_frame(surface, {})

        display_image = set_image.call_args.args[0]
        assert display_image.width() == 1024
        assert controller.current_full_image().width() == 1024
        assert set_image.call_args.kwargs["image_source"] == surface.decode_key

    def test_edit_session_updates_uniforms_without_replacing_texture(self, controller, mocker):
        path = Path("/tmp/session.jpg")
        image = QImage(1024, 768, QImage.Format.Format_RGBA8888)
        surface = _surface(path, image)
        controller._active_source_identity = AssetSourceIdentity.create(
            path,
            width=1024,
            height=768,
            source_mtime_ns=1,
            index_revision=3,
        )
        handle = controller._upsert_render_session(surface, {"Exposure": 0.2})
        controller._image_viewer._current_source = surface.decode_key
        set_image = mocker.patch.object(controller._image_viewer, "set_image")

        acquired = controller.acquire_render_session(path)
        state = controller.update_render_session(acquired, {"Exposure": 0.7})
        assert not controller._lod_timer.isActive()
        restored = controller.finish_render_session(acquired, committed=False)

        assert acquired is handle
        assert handle.current_texture_key == surface.decode_key
        assert state.raw_adjustments["Exposure"] == 0.7
        assert restored.raw_adjustments["Exposure"] == 0.2
        set_image.assert_not_called()

    def test_crop_interaction_defers_lod_until_final_geometry(self, controller):
        path = Path("/tmp/crop-session.jpg")
        image = QImage(1024, 768, QImage.Format.Format_RGBA8888)
        surface = _surface(path, image)
        controller._active_source_identity = AssetSourceIdentity.create(
            path,
            width=1024,
            height=768,
            source_mtime_ns=1,
            index_revision=3,
        )
        controller._upsert_render_session(surface, {"Crop_W": 1.0})
        controller._image_viewer._current_source = surface.decode_key
        acquired = controller.acquire_render_session(path)

        controller.begin_render_session_interaction(acquired)
        state = controller.update_render_session(acquired, {"Crop_W": 0.7})

        assert state.raw_adjustments["Crop_W"] == 0.7
        assert not controller._lod_timer.isActive()
        assert acquired.session_id in controller._render_session_lod_pending

        controller.end_render_session_interaction(acquired)

        assert controller._lod_timer.isActive()
        assert acquired.session_id not in controller._render_session_lod_pending
        controller._lod_timer.stop()
        controller.finish_render_session(acquired, committed=False)

    def test_crop_interaction_defers_in_flight_lod_presentation(
        self,
        controller,
        mocker,
    ):
        path = Path("/tmp/in-flight-crop-session.jpg")
        initial = _surface(
            path,
            QImage(1024, 768, QImage.Format.Format_RGBA8888),
            level=1024,
        )
        upgraded = _surface(
            path,
            QImage(2048, 1536, QImage.Format.Format_RGBA8888),
            level=2048,
        )
        controller._active_source_identity = AssetSourceIdentity.create(
            path,
            width=2048,
            height=1536,
            source_mtime_ns=1,
            index_revision=3,
        )
        handle = controller._upsert_render_session(initial, {"Crop_W": 1.0})
        controller._image_viewer._current_source = initial.decode_key
        acquired = controller.acquire_render_session(path)
        present = mocker.patch.object(controller, "_on_adjusted_image_ready")
        controller._request_generation = 7

        controller.begin_render_session_interaction(acquired)
        controller._on_scheduled_image_ready(7, upgraded)

        assert handle.current_surface is initial
        assert handle.session_id in controller._render_session_pending_surfaces
        present.assert_not_called()

        controller.end_render_session_interaction(acquired)

        # Presentation is now two-phase: ending the gesture releases the
        # deferred surface to the viewer, while the session keeps its last
        # drawn surface until ``stillFramePresented`` acknowledges the upload.
        assert handle.current_surface is initial
        assert handle.session_id not in controller._render_session_pending_surfaces
        present.assert_called_once()
        controller.finish_render_session(acquired, committed=False)

    def test_image_first_render_sets_flag(self, controller):
        """_on_image_first_render should mark image as rendered."""
        controller._on_image_first_render()
        assert controller._image_viewer_rendered is True

    def test_video_first_render_sets_flag(self, controller):
        """_on_video_first_render should mark video as rendered."""
        controller._on_video_first_render()
        assert controller._video_renderer_rendered is True

    def test_show_image_surface_shows_cover_if_not_rendered(self, controller, mocker):
        """show_image_surface should re-show the init cover when image hasn't rendered."""
        mock_show_cover = mocker.patch.object(controller, "_show_detail_init_cover")
        controller._image_viewer_rendered = False
        controller.show_image_surface()
        mock_show_cover.assert_called_once()

    def test_show_image_surface_skips_cover_if_rendered(self, controller, mocker):
        """show_image_surface should NOT re-show cover when image has already rendered."""
        mock_show_cover = mocker.patch.object(controller, "_show_detail_init_cover")
        controller._image_viewer_rendered = True
        controller.show_image_surface()
        mock_show_cover.assert_not_called()

    def test_image_transition_waits_for_matching_content_submission(
        self,
        controller,
        mocker,
    ):
        mock_show_cover = mocker.patch.object(controller, "_show_detail_init_cover")
        mock_hide_cover = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller._image_viewer_rendered = True
        controller._arm_image_transition(22, "image-b")
        controller.show_image_surface()

        controller._image_viewer.firstFrameReady.emit()
        controller._image_viewer.stillFrameSubmitted.emit("image-a", 21)

        assert controller._pending_image_generation == 22
        mock_hide_cover.assert_not_called()

        controller._image_viewer.stillFrameSubmitted.emit("image-b", 22)

        assert controller._pending_image_generation is None
        assert controller._pending_image_key is None
        mock_show_cover.assert_called()
        mock_hide_cover.assert_called_once()

    def test_still_content_barrier_is_armed_before_surface_switch_and_upload(
        self,
        controller,
        mocker,
    ):
        surface = _surface(
            Path("/tmp/content-b.jpg"),
            QImage(64, 48, QImage.Format.Format_RGBA8888),
        )
        controller._present_generation = 24
        controller._request_reason_by_generation[24] = "initial"
        arm = mocker.patch.object(controller, "_arm_image_transition")
        show = mocker.patch.object(controller, "show_image_surface")
        upload = mocker.patch.object(controller._image_viewer, "set_image")
        calls = mocker.Mock()
        calls.attach_mock(arm, "arm")
        calls.attach_mock(show, "show")
        calls.attach_mock(upload, "upload")

        controller._apply_still_frame(surface, {})

        assert calls.mock_calls[:3] == [
            call.arm(24, surface.decode_key),
            call.show(),
            call.upload(
                surface.image,
                {},
                image_source=surface.decode_key,
                source_size=surface.source_size,
                reset_view=True,
            ),
        ]

    def test_show_video_surface_shows_cover_if_not_rendered(self, controller, mocker):
        """show_video_surface should re-show the init cover when video hasn't rendered."""
        mock_show_cover = mocker.patch.object(controller, "_show_detail_init_cover")
        controller._video_renderer_rendered = False
        controller.show_video_surface(interactive=True)
        mock_show_cover.assert_called_once()

    def test_show_video_surface_skips_cover_if_rendered(self, controller, mocker):
        """show_video_surface should NOT re-show cover when video has already rendered."""
        mock_show_cover = mocker.patch.object(controller, "_show_detail_init_cover")
        controller._video_renderer_rendered = True
        controller.show_video_surface(interactive=True)
        mock_show_cover.assert_not_called()

    def test_show_video_surface_rejects_active_video_transition(self, controller):
        controller.begin_video_transition(16, interactive_when_ready=True)
        epoch = controller._surface_transition_epoch

        with pytest.raises(RuntimeError, match="cannot cancel an active video transition"):
            controller.show_video_surface(interactive=True)

        assert controller._pending_video_generation == 16
        assert controller._surface_transition_epoch == epoch

    def test_video_transition_waits_for_matching_submitted_generation(
        self,
        controller,
        mocker,
    ):
        mock_show_cover = mocker.patch.object(controller, "_show_detail_init_cover")
        mock_hide_cover = mocker.patch.object(controller, "_hide_detail_init_cover")
        controls_enabled = mocker.patch.object(
            controller._video_area,
            "set_controls_enabled",
        )

        controller.begin_video_transition(17, interactive_when_ready=True)
        controller._video_area.firstFrameReady.emit()

        assert controller._pending_video_generation == 17
        mock_hide_cover.assert_not_called()

        controller._video_area.surfaceFrameSubmitted.emit(16, 1)
        assert controller._pending_video_generation == 17
        mock_hide_cover.assert_not_called()

        controller._video_area.surfaceFrameSubmitted.emit(17, 1)
        assert controller._pending_video_generation is None
        controls_enabled.assert_called_with(True)
        mock_show_cover.assert_called()
        mock_hide_cover.assert_called_once()

    def test_placeholder_cancels_pending_video_transition(self, controller, mocker):
        mock_hide_cover = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller.begin_video_transition(23, interactive_when_ready=True)

        controller.show_placeholder("Unable to load")

        assert controller._pending_video_generation is None
        mock_hide_cover.assert_called_once()

    def test_active_video_resource_loss_rearms_media_barrier(self, controller, mocker):
        mock_show_cover = mocker.patch.object(controller, "_show_detail_init_cover")
        controller._player_stack.setCurrentWidget(controller._video_area)
        controller._video_renderer_rendered = True

        controller._video_area.surfaceInvalidated.emit(31, 9)

        assert controller._video_renderer_rendered is False
        assert controller._pending_video_generation == 31
        assert controller._pending_video_content_serial == 9
        mock_show_cover.assert_called_once()

    def test_latest_surface_submission_completes_rearmed_switch_barrier(
        self,
        controller,
        mocker,
    ):
        mock_hide_cover = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller._player_stack.setCurrentWidget(controller._video_area)

        controller._video_area.surfaceInvalidated.emit(32, 10)
        controller._video_area.surfaceInvalidated.emit(32, 10)
        controller._video_area.surfaceFrameSubmitted.emit(32, 10)

        assert controller._pending_video_generation is None
        assert controller._pending_video_content_serial is None
        mock_hide_cover.assert_called_once()

    def test_windows_image_reveal_waits_for_next_composition(
        self,
        controller,
        qapp,
        mocker,
    ):
        controller._requires_post_submit_frame = True
        update = mocker.patch.object(controller._image_viewer, "update")
        hide_cover = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller._arm_image_transition(40, "image-40")
        controller.show_image_surface()
        update.reset_mock()

        controller._image_viewer.stillFrameSubmitted.emit("image-40", 40)
        controller._image_viewer.frameSubmitted.emit()

        assert controller._pending_image_generation == 40
        hide_cover.assert_not_called()

        qapp.processEvents()
        update.assert_called()
        hide_cover.assert_not_called()

        controller._image_viewer.frameSubmitted.emit()

        assert controller._pending_image_generation is None
        hide_cover.assert_called_once()

    def test_windows_video_reveal_waits_for_next_active_surface_composition(
        self,
        controller,
        qapp,
        mocker,
    ):
        controller._requires_post_submit_frame = True
        request_update = mocker.patch.object(
            controller._video_area,
            "request_active_surface_update",
        )
        hide_cover = mocker.patch.object(controller, "_hide_detail_init_cover")
        controls_enabled = mocker.patch.object(
            controller._video_area,
            "set_controls_enabled",
        )
        controller.begin_video_transition(41, interactive_when_ready=True)

        controller._video_area.surfaceFrameSubmitted.emit(41, 7)
        controller._video_area.surfaceCompositionSubmitted.emit()

        assert controller._pending_video_generation == 41
        hide_cover.assert_not_called()

        qapp.processEvents()
        request_update.assert_called_once_with()
        hide_cover.assert_not_called()

        controller._video_area.surfaceCompositionSubmitted.emit()

        assert controller._pending_video_generation is None
        assert controller._pending_video_content_serial is None
        controls_enabled.assert_called_with(True)
        hide_cover.assert_called_once()

    def test_invalid_composition_does_not_consume_post_submit_payload(
        self,
        controller,
        qapp,
        mocker,
    ):
        controller._requires_post_submit_frame = True
        mocker.patch.object(
            controller._video_area,
            "request_active_surface_update",
        )
        controller.begin_video_transition(44, interactive_when_ready=False)
        controller._video_area.surfaceFrameSubmitted.emit(44, 9)
        qapp.processEvents()
        payload = (44, 9)
        assert controller._peek_post_submit_payload("video") == payload

        # Simulate a future caller changing ownership without advancing epoch.
        controller._pending_video_generation = 45
        controller._video_area.surfaceCompositionSubmitted.emit()

        assert controller._peek_post_submit_payload("video") == payload
        assert controller._post_submit_armed is True

        controller._pending_video_generation = 44
        controller._video_area.surfaceCompositionSubmitted.emit()

        assert controller._peek_post_submit_payload("video") is None
        assert controller._pending_video_generation is None

    def test_new_transition_cancels_deferred_windows_cover_release(
        self,
        controller,
        qapp,
        mocker,
    ):
        controller._requires_post_submit_frame = True
        update = mocker.patch.object(controller._image_viewer, "update")
        controller._arm_image_transition(42, "image-42")
        controller.show_image_surface()
        update.reset_mock()
        controller._image_viewer.stillFrameSubmitted.emit("image-42", 42)

        controller.show_placeholder("cancelled")
        qapp.processEvents()
        controller._image_viewer.frameSubmitted.emit()

        update.assert_not_called()
        assert controller._pending_image_generation is None

    def test_video_invalidation_replaces_deferred_windows_release_epoch(
        self,
        controller,
        qapp,
        mocker,
    ):
        controller._requires_post_submit_frame = True
        request_update = mocker.patch.object(
            controller._video_area,
            "request_active_surface_update",
        )
        hide_cover = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller.begin_video_transition(43, interactive_when_ready=False)
        controller._video_area.surfaceFrameSubmitted.emit(43, 8)

        controller._video_area.surfaceInvalidated.emit(43, 8)
        qapp.processEvents()
        controller._video_area.surfaceCompositionSubmitted.emit()

        request_update.assert_not_called()
        hide_cover.assert_not_called()
        assert controller._pending_video_generation == 43

        controller._video_area.surfaceFrameSubmitted.emit(43, 8)
        qapp.processEvents()
        request_update.assert_called_once_with()
        controller._video_area.surfaceCompositionSubmitted.emit()

        assert controller._pending_video_generation is None
        hide_cover.assert_called_once()

    def test_image_first_render_hides_cover_when_image_visible(self, controller, mocker):
        """_on_image_first_render should hide cover when image is current widget."""
        mock_hide = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller._player_stack.setCurrentWidget(controller._image_viewer)
        controller._on_image_first_render()
        mock_hide.assert_called_once()

    def test_image_first_render_skips_hide_when_video_visible(self, controller, mocker):
        """_on_image_first_render should NOT hide cover when video is current widget."""
        mock_hide = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller._player_stack.setCurrentWidget(controller._video_area)
        controller._on_image_first_render()
        mock_hide.assert_not_called()
        # But the flag should still be set
        assert controller._image_viewer_rendered is True

    def test_video_first_render_hides_cover_when_video_visible(self, controller, mocker):
        """_on_video_first_render should hide cover when video is current widget."""
        mock_hide = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller._player_stack.setCurrentWidget(controller._video_area)
        controller._on_video_first_render()
        mock_hide.assert_called_once()

    def test_video_first_render_skips_hide_when_image_visible(self, controller, mocker):
        """_on_video_first_render should NOT hide cover when image is current widget."""
        mock_hide = mocker.patch.object(controller, "_hide_detail_init_cover")
        controller._player_stack.setCurrentWidget(controller._image_viewer)
        controller._on_video_first_render()
        mock_hide.assert_not_called()
        assert controller._video_renderer_rendered is True


def test_init_cover_stays_below_face_name_overlay_and_does_not_take_mouse(
    qapp,
    monkeypatch,
):
    monkeypatch.setattr(
        "iPhoto.gui.ui.widgets.detail_page.VideoArea",
        _FakeVideoArea,
    )
    main_window = QWidget()
    image_viewer = _FakeImageViewer()
    detail = DetailPageWidget(main_window, image_viewer=image_viewer)
    detail.resize(640, 480)
    detail.show()
    detail.player_stack.setCurrentWidget(image_viewer)
    qapp.processEvents()

    detail.face_name_overlay.set_annotations(
        [
            AssetFaceAnnotation(
                face_id="face-1",
                person_id="person-1",
                display_name="Julie",
                box_x=80,
                box_y=80,
                box_w=120,
                box_h=90,
                image_width=420,
                image_height=320,
            )
        ]
    )
    detail.face_name_overlay.set_overlay_active(True)
    image_viewer.viewTransformChanged.emit()
    qapp.processEvents()

    assert detail.face_name_overlay._states["face-1"].layout.chip_rect.isEmpty() is False

    detail.show_rhi_init_cover()
    qapp.processEvents()

    assert detail._rhi_init_cover is not None
    assert detail._rhi_init_cover.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert detail.face_name_overlay.isVisible() is True
    persistent_cover = detail._rhi_init_cover
    player_layout = detail.player_container.layout()
    layout_count = player_layout.count()

    margin_point = _chip_margin_point(detail.face_name_overlay)
    _send_mouse_move_to_overlay_point(detail._rhi_init_cover, detail.face_name_overlay, margin_point)
    qapp.processEvents()

    assert detail.face_name_overlay._hovered_face_id == "face-1"
    _assert_ibeam_cursor()

    detail.hide_rhi_init_cover()
    detail.show_rhi_init_cover()
    qapp.processEvents()

    assert detail._rhi_init_cover is persistent_cover
    assert player_layout.count() == layout_count
    assert detail._rhi_init_cover.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    _send_mouse_move_to_overlay_point(image_viewer, detail.face_name_overlay, margin_point)
    qapp.processEvents()
    assert detail.face_name_overlay._hovered_face_id == "face-1"
    _assert_ibeam_cursor()

    image_viewer.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
    detail.face_name_overlay.refresh_view_state()
    qapp.processEvents()

    assert not image_viewer.testAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)

    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()


class TestPlaceholderMessage:
    def test_show_placeholder_supports_custom_message(self, controller):
        controller.show_placeholder("Writing data, please wait...")

        assert controller._placeholder.text() == "Writing data, please wait..."

    def test_show_placeholder_restores_default_message(self, controller):
        controller.show_placeholder("Writing data, please wait...")

        controller.show_placeholder()

        assert controller._placeholder.text() == "placeholder"
