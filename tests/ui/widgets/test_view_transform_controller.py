import pytest
from PySide6.QtCore import QPointF

from iPhoto.gui.ui.widgets.view_transform_controller import ViewTransformController


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


def test_image_viewport_roundtrip_with_non_square_target_and_pan_zoom() -> None:
    viewer = FakeViewer(width=200, height=100, dpr=2.0)
    controller = make_controller(viewer, (600.0, 250.0), texture_size=(400, 300))
    controller.set_zoom_factor_direct(1.7)
    controller.set_pan_pixels(QPointF(37.0, -22.0))

    viewport_point = controller.convert_image_to_viewport(280.0, 140.0)
    image_point = controller.convert_viewport_to_image(viewport_point)

    assert image_point.x() == pytest.approx(280.0)
    assert image_point.y() == pytest.approx(140.0)


def test_shader_fragment_mapping_matches_view_transform_for_both_origins() -> None:
    viewer = FakeViewer(width=200, height=100, dpr=2.0)
    controller = make_controller(viewer, (600.0, 250.0), texture_size=(400, 300))
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
