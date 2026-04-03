"""Tests for LUT caching in the GL adjustment applicator."""

from __future__ import annotations

from iPhoto.gui.ui.widgets.gl_image_viewer.adjustment_applicator import AdjustmentApplicator


class _RendererStub:
    def __init__(self) -> None:
        self.curve_uploads = 0
        self.levels_uploads = 0

    def upload_curve_lut(self, _lut) -> None:
        self.curve_uploads += 1

    def upload_levels_lut(self, _lut) -> None:
        self.levels_uploads += 1


def _applicator(renderer: _RendererStub) -> AdjustmentApplicator:
    return AdjustmentApplicator(
        renderer_provider=lambda: renderer,
        make_current=lambda: None,
        done_current=lambda: None,
    )


def test_disabled_curve_lut_uploads_once_until_cache_is_invalidated() -> None:
    renderer = _RendererStub()
    applicator = _applicator(renderer)

    applicator.update_curve_lut_if_needed({})
    applicator.update_curve_lut_if_needed({})

    assert renderer.curve_uploads == 1

    applicator.invalidate_cache()
    applicator.update_curve_lut_if_needed({})

    assert renderer.curve_uploads == 2


def test_disabled_levels_lut_uploads_once_until_cache_is_invalidated() -> None:
    renderer = _RendererStub()
    applicator = _applicator(renderer)

    applicator.update_levels_lut_if_needed({})
    applicator.update_levels_lut_if_needed({})

    assert renderer.levels_uploads == 1

    applicator.invalidate_cache()
    applicator.update_levels_lut_if_needed({})

    assert renderer.levels_uploads == 2
