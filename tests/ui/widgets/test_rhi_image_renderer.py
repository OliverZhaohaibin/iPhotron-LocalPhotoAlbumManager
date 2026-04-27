"""Tests for the QRhi-backed image renderer helpers."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for QRhi renderer tests")

from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import QColor

from iPhoto.gui.ui.widgets.rhi_image_renderer import RhiImageRenderer


class _FakeBuffer:
    def __init__(self, create_result: bool | None = True) -> None:
        self.create_result = create_result
        self.create_calls = 0
        self.destroyed = False

    def create(self) -> bool | None:
        self.create_calls += 1
        return self.create_result

    def destroy(self) -> None:
        self.destroyed = True


class _FakeRhi:
    def __init__(self, buffer: _FakeBuffer | None) -> None:
        self.buffer = buffer
        self.new_buffer_calls = 0

    def newBuffer(self, *args):  # noqa: N802 - mirrors QRhi API
        self.new_buffer_calls += 1
        return self.buffer


class _FakeResourceUpdateBatch:
    def __init__(self) -> None:
        self.dynamic_updates: list[tuple[object, int, int, bytes]] = []

    def updateDynamicBuffer(self, buffer, offset, size, data):  # noqa: N802
        self.dynamic_updates.append((buffer, offset, size, data))


class _FakeRenderRhi:
    def __init__(self) -> None:
        self.batch = _FakeResourceUpdateBatch()

    def nextResourceUpdateBatch(self):  # noqa: N802 - mirrors QRhi API
        return self.batch


class _FakeRenderTarget:
    def __init__(self, width: int = 400, height: int = 300) -> None:
        self._size = QSize(width, height)

    def pixelSize(self):  # noqa: N802 - mirrors QRhi API
        return self._size


class _FakeCommandBuffer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | tuple[object, ...]]] = []

    def resourceUpdate(self, batch):  # noqa: N802 - mirrors QRhi API
        self.calls.append(("resourceUpdate", batch))

    def beginPass(self, render_target, clear_color, depth_stencil):  # noqa: N802
        self.calls.append(("beginPass", (render_target, clear_color, depth_stencil)))

    def setGraphicsPipeline(self, pipeline):  # noqa: N802
        self.calls.append(("pipeline", pipeline))

    def setShaderResources(self, bindings):  # noqa: N802
        self.calls.append(("shaderResources", bindings))

    def setViewport(self, viewport):  # noqa: N802
        self.calls.append(("viewport", viewport))

    def setVertexInput(self, start_binding, bindings):  # noqa: N802
        self.calls.append(("vertexInput", (start_binding, bindings)))

    def draw(self, vertex_count):  # noqa: N802
        self.calls.append(("draw", vertex_count))

    def endPass(self):  # noqa: N802
        self.calls.append(("endPass", ()))


def test_overlay_buffer_creation_failure_leaves_no_stale_buffer() -> None:
    renderer = RhiImageRenderer()
    buffer = _FakeBuffer(create_result=False)
    renderer._rhi = _FakeRhi(buffer)  # type: ignore[assignment]

    assert renderer._ensure_overlay_buffer(64) is False
    assert renderer._overlay_vbuf is None
    assert renderer._overlay_vbuf_capacity == 0
    assert buffer.destroyed is True


def test_overlay_buffer_reuses_existing_capacity() -> None:
    renderer = RhiImageRenderer()
    buffer = _FakeBuffer(create_result=True)
    fake_rhi = _FakeRhi(buffer)
    renderer._rhi = fake_rhi  # type: ignore[assignment]

    assert renderer._ensure_overlay_buffer(64) is True
    assert renderer._overlay_vbuf is buffer
    assert renderer._overlay_vbuf_capacity >= 4096

    assert renderer._ensure_overlay_buffer(32) is True
    assert fake_rhi.new_buffer_calls == 1
    assert buffer.create_calls == 1


def test_overlay_vertices_reject_non_finite_rect_values() -> None:
    vertices = RhiImageRenderer._build_overlay_vertices(
        view_width=400.0,
        view_height=300.0,
        crop_rect={"left": 10.0, "top": math.nan, "right": 200.0, "bottom": 220.0},
        faded=False,
    )

    assert vertices == []


def test_overlay_vertices_clamp_to_viewport() -> None:
    vertices = RhiImageRenderer._build_overlay_vertices(
        view_width=400.0,
        view_height=300.0,
        crop_rect={"left": -50.0, "top": 30.0, "right": 450.0, "bottom": 260.0},
        faded=False,
    )

    assert vertices
    assert all(math.isfinite(value) for value in vertices)
    assert all(-1.0 <= value <= 1.0 for value in vertices[0::6])
    assert all(-1.0 <= value <= 1.0 for value in vertices[1::6])


def test_overlay_vertices_accept_swapped_rect_edges() -> None:
    vertices = RhiImageRenderer._build_overlay_vertices(
        view_width=400.0,
        view_height=300.0,
        crop_rect={"left": 300.0, "top": 240.0, "right": 80.0, "bottom": 60.0},
        faded=True,
    )

    assert vertices
    assert all(math.isfinite(value) for value in vertices)


def test_overlay_vertices_active_draws_border_corners_and_edge_handles() -> None:
    vertices = RhiImageRenderer._build_overlay_vertices(
        view_width=400.0,
        view_height=300.0,
        crop_rect={"left": 100.0, "top": 75.0, "right": 300.0, "bottom": 225.0},
        faded=False,
    )

    # 4 mask quads + 4 border quads + 4 corner handles + 4 edge handles.
    assert len(vertices) // 6 == 16 * 6
    colours = [tuple(vertices[index + 2:index + 6]) for index in range(0, len(vertices), 6)]
    assert colours.count((0.0, 0.0, 0.0, 0.55)) == 4 * 6
    assert colours.count((1.0, 0.85, 0.2, 1.0)) == 12 * 6


def test_overlay_vertices_faded_hides_yellow_handles() -> None:
    vertices = RhiImageRenderer._build_overlay_vertices(
        view_width=400.0,
        view_height=300.0,
        crop_rect={"left": 100.0, "top": 75.0, "right": 300.0, "bottom": 225.0},
        faded=True,
    )

    assert len(vertices) // 6 == 4 * 6
    colours = [tuple(vertices[index + 2:index + 6]) for index in range(0, len(vertices), 6)]
    assert set(colours) == {(0.0, 0.0, 0.0, 1.0)}


def test_render_rebinds_overlay_shader_resources(monkeypatch) -> None:
    renderer = RhiImageRenderer()
    main_pipeline = object()
    overlay_pipeline = object()
    main_bindings = object()
    overlay_bindings = object()
    main_vbuf = object()
    overlay_vbuf = object()
    fake_rhi = _FakeRenderRhi()
    fake_cb = _FakeCommandBuffer()

    renderer._rhi = fake_rhi  # type: ignore[assignment]
    renderer._pipeline = main_pipeline  # type: ignore[assignment]
    renderer._overlay_pipeline = overlay_pipeline  # type: ignore[assignment]
    renderer._srb = main_bindings  # type: ignore[assignment]
    renderer._overlay_srb = overlay_bindings  # type: ignore[assignment]
    renderer._vbuf = main_vbuf  # type: ignore[assignment]
    renderer._overlay_vbuf = overlay_vbuf  # type: ignore[assignment]
    renderer._overlay_vbuf_capacity = 4096
    monkeypatch.setattr(renderer, "_flush_pending_texture_uploads", lambda batch: None)
    monkeypatch.setattr(renderer, "_update_uniforms", lambda batch, **kwargs: None)

    renderer.render(
        cb=fake_cb,
        render_target=_FakeRenderTarget(),
        clear_color=QColor(0, 0, 0),
        view_width=400.0,
        view_height=300.0,
        scale=1.0,
        pan=QPointF(0.0, 0.0),
        adjustments={},
        crop_rect={"left": 100.0, "top": 75.0, "right": 300.0, "bottom": 225.0},
    )

    pipeline_calls = [value for name, value in fake_cb.calls if name == "pipeline"]
    bindings_calls = [value for name, value in fake_cb.calls if name == "shaderResources"]
    draw_calls = [value for name, value in fake_cb.calls if name == "draw"]

    assert pipeline_calls == [main_pipeline, overlay_pipeline]
    assert bindings_calls == [main_bindings, overlay_bindings]
    assert draw_calls == [6, 16 * 6]
    assert len(fake_rhi.batch.dynamic_updates) == 1

    overlay_pipeline_index = fake_cb.calls.index(("pipeline", overlay_pipeline))
    overlay_bindings_index = fake_cb.calls.index(("shaderResources", overlay_bindings))
    overlay_draw_index = fake_cb.calls.index(("draw", 16 * 6))
    assert overlay_pipeline_index < overlay_bindings_index < overlay_draw_index
