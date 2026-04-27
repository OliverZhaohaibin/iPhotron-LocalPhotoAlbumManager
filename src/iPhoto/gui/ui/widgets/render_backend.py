"""Compatibility re-export for widget-local backend imports."""

from __future__ import annotations

from ...render_backend import (  # noqa: F401
    is_opengl_api,
    qrhi_api_name,
    select_qrhi_widget_api,
    selected_rhi_backend_name,
    should_configure_global_desktop_opengl,
)
