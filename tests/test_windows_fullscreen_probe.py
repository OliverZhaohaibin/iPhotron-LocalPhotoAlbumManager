"""Probe configuration must match the observed Windows production path."""

from PySide6.QtGui import QSurfaceFormat

from tools.windows_fullscreen_probe import probe_surface_format


def test_default_probe_does_not_request_a_different_gl_profile():
    actual = probe_surface_format(1)
    default = QSurfaceFormat()
    assert actual.profile() == default.profile()
    assert actual.version() == default.version()
    assert actual.alphaBufferSize() == 8
    assert actual.depthBufferSize() == 24
    assert actual.stencilBufferSize() == 8
    assert actual.swapInterval() == 1


def test_vsync_comparison_changes_only_requested_interval():
    baseline, comparison = probe_surface_format(1), probe_surface_format(0)
    assert comparison.swapInterval() == 0
    assert baseline.profile() == comparison.profile()
    assert baseline.version() == comparison.version()
    assert baseline.alphaBufferSize() == comparison.alphaBufferSize()
