from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt

from iPhoto.gui.ui.widgets.view_transform_controller import (
    ViewTransformController,
    compute_rotation_cover_scale,
)


class FakeViewer:
    def __init__(self, *, width: int = 100, height: int = 100, dpr: float = 2.0) -> None:
        self._width = width
        self._height = height
        self._dpr = dpr
        self.update_count = 0

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height

    def devicePixelRatioF(self) -> float:
        return self._dpr

    def update(self) -> None:
        self.update_count += 1

    def setCursor(self, _cursor) -> None:  # noqa: N802 - Qt-compatible test seam
        return None

    def unsetCursor(self) -> None:  # noqa: N802 - Qt-compatible test seam
        return None


def make_controller(
    viewer: FakeViewer,
    render_target: tuple[float, float],
    texture_size: tuple[int, int] = (100, 100),
) -> ViewTransformController:
    return ViewTransformController(
        viewer,
        texture_size_provider=lambda: texture_size,
        display_texture_size_provider=lambda: texture_size,
        device_view_size_provider=lambda: render_target,
        on_zoom_changed=lambda _zoom: None,
    )


def _shader_uv_for_fragment(
    *,
    gl_frag: QPointF,
    origin_top_left: bool,
    view_size: tuple[float, float],
    texture_size: tuple[float, float],
    scale: float,
    pan: QPointF,
) -> tuple[float, float]:
    frag_x = float(gl_frag.x()) - 0.5
    frag_y = float(gl_frag.y()) - 0.5
    view_w, view_h = view_size
    if not origin_top_left:
        frag_y = view_h - 1.0 - frag_y
    world_x = frag_x - (view_w * 0.5)
    world_y = (view_h * 0.5) - frag_y
    screen_x = world_x - float(pan.x())
    screen_y = world_y - float(pan.y())
    tex_w, tex_h = texture_size
    tex_x = (screen_x / scale) + (tex_w * 0.5)
    tex_y = (-screen_y / scale) + (tex_h * 0.5)
    return tex_x / tex_w, tex_y / tex_h


def test_viewport_conversions_use_render_target_size_not_widget_size() -> None:
    viewer = FakeViewer(width=100, height=100, dpr=2.0)
    controller = make_controller(viewer, (300.0, 200.0))

    viewport_center = controller.convert_image_to_viewport(50.0, 50.0)

    assert viewport_center.x() == pytest.approx(50.0)
    assert viewport_center.y() == pytest.approx(50.0)
    device_center = controller.viewport_logical_to_device(viewport_center)
    assert device_center.x() == pytest.approx(150.0)
    assert device_center.y() == pytest.approx(100.0)
    delta_device = controller.viewport_delta_logical_to_device(QPointF(10.0, 10.0))
    assert delta_device.x() == pytest.approx(30.0)
    assert delta_device.y() == pytest.approx(20.0)
    image_center = controller.convert_viewport_to_image(viewport_center)
    assert image_center.x() == pytest.approx(50.0)
    assert image_center.y() == pytest.approx(50.0)


def test_zoom_anchor_defaults_to_render_target_center() -> None:
    viewer = FakeViewer(width=100, height=100, dpr=2.0)
    controller = make_controller(viewer, (300.0, 200.0))

    assert controller.set_zoom(2.0) is True

    image_center = controller.convert_viewport_to_image(QPointF(50.0, 50.0))
    assert image_center.x() == pytest.approx(50.0)
    assert image_center.y() == pytest.approx(50.0)


def test_pan_reports_change_only_after_nonzero_drag() -> None:
    viewer = FakeViewer()
    controller = make_controller(viewer, (200.0, 200.0))
    press = Mock()
    press.button.return_value = Qt.MouseButton.LeftButton
    press.position.return_value = QPointF(25.0, 25.0)
    stationary_move = Mock()
    stationary_move.position.return_value = QPointF(25.0, 25.0)
    drag_move = Mock()
    drag_move.position.return_value = QPointF(35.0, 30.0)

    controller.handle_mouse_press(press)

    assert controller.handle_mouse_move(stationary_move) is False
    assert controller.handle_mouse_move(drag_move) is True
    assert controller.get_pan_pixels() != QPointF()


def test_wheel_reports_only_zoom_changes() -> None:
    viewer = FakeViewer()
    next_item = Mock()
    controller = ViewTransformController(
        viewer,
        texture_size_provider=lambda: (100, 100),
        display_texture_size_provider=lambda: (100, 100),
        device_view_size_provider=lambda: (200.0, 200.0),
        on_zoom_changed=lambda _zoom: None,
        on_next_item=next_item,
    )
    event = Mock()
    event.angleDelta.return_value = QPoint(0, -120)
    event.position.return_value = QPointF(50.0, 50.0)

    controller.set_wheel_action("navigate")
    assert controller.handle_wheel(event) is False
    next_item.assert_called_once_with()

    controller.set_wheel_action("zoom")
    assert controller.handle_wheel(event) is True


def test_image_viewport_roundtrip_with_non_square_target_and_pan_zoom() -> None:
    viewer = FakeViewer(width=200, height=100, dpr=2.0)
    controller = make_controller(viewer, (600.0, 250.0), texture_size=(400, 300))
    controller.set_zoom_factor_direct(1.7)
    controller.set_pan_pixels(QPointF(37.0, -22.0))

    viewport_point = controller.convert_image_to_viewport(280.0, 140.0)
    image_point = controller.convert_viewport_to_image(viewport_point)

    assert image_point.x() == pytest.approx(280.0)
    assert image_point.y() == pytest.approx(140.0)


def test_frame_texture_rect_centers_with_non_unit_cover_scale() -> None:
    viewer = FakeViewer(width=200, height=100, dpr=2.0)
    controller = make_controller(
        viewer,
        (600.0, 250.0),
        texture_size=(400, 300),
    )
    controller.set_image_cover_scale(1.35)
    crop_rect = QRectF(180.0, 105.0, 160.0, 120.0)

    assert controller.frame_texture_rect(crop_rect) is True

    viewport_center = controller.convert_image_to_viewport(
        crop_rect.center().x(),
        crop_rect.center().y(),
    )
    assert viewport_center.x() == pytest.approx(100.0, abs=1e-6)
    assert viewport_center.y() == pytest.approx(50.0, abs=1e-6)
    fit = controller.compute_texture_rect_fit(crop_rect)
    assert fit is not None
    _zoom, target_scale = fit
    assert controller.get_effective_scale() == pytest.approx(target_scale)


@pytest.mark.parametrize("angle", [-11.0, -5.0, 5.0, 11.0])
def test_straighten_cover_factor_is_independent_of_lod(angle: float) -> None:
    factors = [
        compute_rotation_cover_scale(size, angle)
        for size in ((1024, 768), (2048, 1536), (4000, 3000))
    ]

    assert factors[0] > 1.0
    assert factors[1:] == pytest.approx([factors[0], factors[0]])


def test_transform_transaction_coalesces_automatic_publication() -> None:
    viewer = FakeViewer(width=200, height=100, dpr=2.0)
    zoom_events: list[float] = []
    transform_events: list[None] = []
    controller = ViewTransformController(
        viewer,
        texture_size_provider=lambda: (400, 300),
        display_texture_size_provider=lambda: (400, 300),
        device_view_size_provider=lambda: (600.0, 250.0),
        on_zoom_changed=zoom_events.append,
        on_view_transform_changed=lambda: transform_events.append(None),
    )

    with controller.transform_transaction(emit_zoom=False):
        controller.set_image_cover_scale(1.2)
        controller.set_zoom_factor_direct(1.7)
        controller.set_pan_pixels(QPointF(20.0, -10.0))

    assert viewer.update_count == 1
    assert zoom_events == []
    assert transform_events == [None]

    controller.set_zoom_factor_direct(1.8)
    assert viewer.update_count == 2
    assert zoom_events == [pytest.approx(1.8)]
    assert transform_events == [None, None]


def test_shader_fragment_mapping_matches_view_transform_for_both_origins() -> None:
    viewer = FakeViewer(width=200, height=100, dpr=2.0)
    controller = make_controller(viewer, (600.0, 250.0), texture_size=(400, 300))
    controller.set_image_cover_scale(
        compute_rotation_cover_scale((400, 300), 6.0)
    )
    controller.set_zoom_factor_direct(1.4)
    controller.set_pan_pixels(QPointF(-41.0, 18.0))

    image_x, image_y = 260.0, 210.0
    viewport_point = controller.convert_image_to_viewport(image_x, image_y)
    device_point = controller.viewport_logical_to_device(viewport_point)
    view_w, view_h = controller.get_view_dimensions_device_px()
    scale = controller.get_effective_scale()
    pan = controller.get_pan_pixels()

    rhi_uv = _shader_uv_for_fragment(
        gl_frag=QPointF(device_point.x() + 0.5, device_point.y() + 0.5),
        origin_top_left=True,
        view_size=(view_w, view_h),
        texture_size=(400.0, 300.0),
        scale=scale,
        pan=pan,
    )
    gl_bottom_y = view_h - 1.0 - device_point.y()
    raw_gl_uv = _shader_uv_for_fragment(
        gl_frag=QPointF(device_point.x() + 0.5, gl_bottom_y + 0.5),
        origin_top_left=False,
        view_size=(view_w, view_h),
        texture_size=(400.0, 300.0),
        scale=scale,
        pan=pan,
    )

    expected = (image_x / 400.0, image_y / 300.0)
    assert rhi_uv == pytest.approx(expected)
    assert raw_gl_uv == pytest.approx(expected)
