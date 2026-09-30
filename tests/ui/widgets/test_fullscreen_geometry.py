"""Geometry contracts spanning real viewer transforms and renderer arguments."""

from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPointF, QSize

from iPhoto.gui.ui.widgets.gl_image_viewer import GLImageViewer
from iPhoto.gui.ui.widgets.view_transform_controller import compute_rotation_cover_scale


@pytest.mark.parametrize("angle", [-30.0, -10.0, 0.0, 10.0, 30.0])
def test_cover_is_invariant_under_lod_and_quarter_turn(angle):
    expected = compute_rotation_cover_scale((800, 600), angle)
    for size in [(1600, 1200), (4000, 3000), (600, 800), (1200, 1600)]:
        assert compute_rotation_cover_scale(size, angle) == pytest.approx(expected)


def make_viewer(qapp, monkeypatch, *, raw_gl=True):
    viewer = GLImageViewer()
    viewer.resize(600, 400)
    viewer._uses_raw_gl = raw_gl
    viewer._gl_initialized = True
    viewer._gl_funcs = Mock()
    renderer = Mock()
    renderer.has_texture.return_value = True
    renderer.texture_size.return_value = (800, 600)
    renderer.take_still_upload_result.return_value = None
    viewer._renderer = renderer
    target = Mock()
    target.pixelSize.return_value = QSize(1200, 800)
    monkeypatch.setattr(viewer, "renderTarget", lambda: target)
    return viewer, renderer, target


@pytest.mark.parametrize("raw_gl", [True, False])
@pytest.mark.parametrize("angle", [-15.0, 15.0])
@pytest.mark.parametrize("rotate", [0, 1, 2, 3])
def test_cropped_straightened_draw_matches_cpu_and_fits_each_target(
    qapp, monkeypatch, raw_gl, angle, rotate
):
    viewer, renderer, target = make_viewer(qapp, monkeypatch, raw_gl=raw_gl)
    viewer._adjustments = {
        "Crop_CX": 0.68,
        "Crop_CY": 0.62,
        "Crop_W": 0.5,
        "Crop_H": 0.6,
        "Crop_Straighten": angle,
        "Crop_Rotate90": rotate,
    }
    for size in [(1200, 800), (1920, 1080), (1200, 800)]:
        target.pixelSize.return_value = QSize(*size)
        viewer.request_viewport_relayout(reset_view=True)
        viewer.render(Mock())
        args = renderer.render.call_args.kwargs
        assert args["img_scale"] == 1.0
        crop = viewer._compute_crop_rect_pixels()
        tex_w, tex_h = args["logical_tex_size"]
        # Independently invert the shader's view -> texture mapping.
        x = size[0] / 2 + args["pan"].x() + (crop.center().x() - tex_w / 2) * args["scale"]
        y = size[1] / 2 - args["pan"].y() + (crop.center().y() - tex_h / 2) * args["scale"]
        assert x == pytest.approx(size[0] / 2, abs=1)
        assert y == pytest.approx(size[1] / 2, abs=1)
        assert crop.width() * args["scale"] <= size[0] + 1
        assert crop.height() * args["scale"] <= size[1] + 1
        assert min(
            size[0] / (crop.width() * args["scale"]), size[1] / (crop.height() * args["scale"])
        ) == pytest.approx(1)
        cpu = viewer._transform_controller.convert_image_to_viewport(
            crop.center().x(), crop.center().y()
        )
        assert cpu.x() == pytest.approx(300, abs=1)
        assert cpu.y() == pytest.approx(200, abs=1)


def test_tiny_crop_fit_can_exceed_interactive_zoom_limit(qapp, monkeypatch):
    viewer, _renderer, _ = make_viewer(qapp, monkeypatch)
    viewer._adjustments = {
        "Crop_CX": 0.7,
        "Crop_CY": 0.3,
        "Crop_W": 0.01,
        "Crop_H": 0.01,
        "Crop_Straighten": 10.0,
    }
    viewer.request_viewport_relayout(reset_view=True)
    viewer.render(Mock())
    zoom = viewer.zoom_factor()
    assert zoom > 16
    viewer.set_zoom(zoom / 1.1, QPointF(300, 200))
    assert viewer.zoom_factor() == pytest.approx(zoom / 1.1)


@pytest.mark.parametrize("raw_gl", [True, False])
def test_manual_view_survives_resident_lod_activation(qapp, monkeypatch, raw_gl):
    viewer, renderer, _ = make_viewer(qapp, monkeypatch, raw_gl=raw_gl)
    viewer._adjustments = {"Crop_Straighten": 15.0}
    viewer.render(Mock())
    viewer.set_zoom(2.2, QPointF(123, 217))
    before = viewer.viewport_to_image(QPointF(300, 200), image_width=4000, image_height=3000)
    observations = []
    viewer.viewTransformChanged.connect(
        lambda: observations.append(
            viewer.viewport_to_image(QPointF(300, 200), image_width=4000, image_height=3000)
        )
    )
    viewer._pending_resident_activation = "higher-lod"

    def activate(_key):
        renderer.texture_size.return_value = (1600, 1200)
        return True

    monkeypatch.setattr(viewer._texture_manager, "activate_resident_texture", activate)
    viewer.render(Mock())
    assert viewer.zoom_factor() == pytest.approx(2.2)
    assert len(observations) == 1
    assert observations[0].x() == pytest.approx(before.x(), abs=1e-5)
    assert observations[0].y() == pytest.approx(before.y(), abs=1e-5)


def test_failed_gl_draw_closes_pass_and_does_not_acknowledge_content(qapp, monkeypatch):
    viewer, renderer, _ = make_viewer(qapp, monkeypatch)
    renderer.render.side_effect = RuntimeError("draw failed")
    viewer._still_presentation_pending = True
    command_buffer = Mock()
    with pytest.raises(RuntimeError, match="draw failed"):
        viewer.render(command_buffer)
    command_buffer.endExternal.assert_called_once()
    command_buffer.endPass.assert_called_once()
    assert viewer._rendered_content_identity is None
    assert viewer._still_presentation_pending


def test_fullscreen_fit_ends_drag_and_notifies_only_final_geometry(qapp, monkeypatch):
    viewer, renderer, _ = make_viewer(qapp, monkeypatch)
    viewer._adjustments = {
        "Crop_CX": 0.7,
        "Crop_CY": 0.6,
        "Crop_W": 0.5,
        "Crop_H": 0.5,
        "Crop_Straighten": 10.0,
    }
    viewer._transform_controller._is_panning = True
    observations = []
    viewer.viewTransformChanged.connect(lambda: observations.append(renderer.render.call_count))
    viewer.request_viewport_relayout(reset_view=True)
    assert not viewer._transform_controller._is_panning
    viewer.render(Mock())
    assert observations == [1]


def test_lod_upload_exception_draws_resident_fallback_without_false_submission(qapp, monkeypatch):
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QSignalSpy

    viewer, renderer, _ = make_viewer(qapp, monkeypatch)
    viewer.render(Mock())
    image = QImage(1600, 1200, QImage.Format.Format_RGBA8888)
    image.fill(0xFF20DD20)
    viewer.set_image(image, {}, image_source="failed-lod", reset_view=False)
    renderer.upload_still_texture.side_effect = RuntimeError("allocation failed")
    failures = QSignalSpy(viewer.stillTextureAllocationFailed)
    renderer.render.reset_mock()
    viewer.render(Mock())
    renderer.render.assert_called_once()
    assert renderer.texture_size() == (800, 600)
    assert failures.count() == 1
    assert viewer._rendered_content_identity is None
    assert not viewer._texture_manager.needs_texture_upload()


def test_diagnostics_are_opt_in_and_do_not_interfere_with_rendering(qapp, monkeypatch):
    from iPhoto.gui import fullscreen_diagnostics

    viewer, renderer, _ = make_viewer(qapp, monkeypatch)
    events = []
    monkeypatch.setattr(
        fullscreen_diagnostics, "emit_detail_event", lambda *a, **kw: events.append((a, kw))
    )
    monkeypatch.delenv("IPHOTO_FULLSCREEN_DIAG", raising=False)
    viewer._trace_fullscreen("fit_requested")
    assert not events
    monkeypatch.setenv("IPHOTO_FULLSCREEN_DIAG", "1")
    viewer._trace_fullscreen("fit_requested")
    assert [a[0] for a, _ in events] == ["fullscreen_environment", "fullscreen_trace"]
    assert events[-1][1]["texture_size"] == (800, 600)
    # Driver/query failures must never turn a diagnostic run into a render failure.
    monkeypatch.setattr(
        fullscreen_diagnostics, "_trace_viewer", Mock(side_effect=RuntimeError("query"))
    )
    viewer.render(Mock())
    renderer.render.assert_called_once()
