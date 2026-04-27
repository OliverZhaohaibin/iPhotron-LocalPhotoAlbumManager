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


def make_controller(viewer: FakeViewer, render_target: tuple[float, float]) -> ViewTransformController:
    return ViewTransformController(
        viewer,
        texture_size_provider=lambda: (100, 100),
        display_texture_size_provider=lambda: (100, 100),
        device_view_size_provider=lambda: render_target,
        on_zoom_changed=lambda _zoom: None,
    )


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
