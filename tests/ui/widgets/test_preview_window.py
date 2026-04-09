from __future__ import annotations

from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for preview window tests", exc_type=ImportError)

from iPhoto.gui.ui.widgets.preview_window import PreviewWindow


def test_do_close_unloads_media_and_hides_window() -> None:
    window = PreviewWindow.__new__(PreviewWindow)
    window._close_timer = Mock()
    window._media = Mock()
    window._rhi_popup = Mock()
    window.hide = Mock()

    PreviewWindow._do_close(window)

    window._close_timer.stop.assert_called_once_with()
    window._media.unload.assert_called_once_with()
    window._rhi_popup.close_preview.assert_called_once_with()
    window.hide.assert_called_once_with()
