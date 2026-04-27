"""Tests for the QRhi-backed image renderer helpers."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for QRhi renderer tests")

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
