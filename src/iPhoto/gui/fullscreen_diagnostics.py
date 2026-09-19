"""Opt-in, bounded fullscreen geometry and OpenGL diagnostics (no media pixels)."""

from __future__ import annotations

import logging
import os
import time

from .detail_profile import emit_detail_event


def trace_viewer(viewer, event: str, *, gl_state: bool = False) -> None:
    if os.environ.get("IPHOTO_FULLSCREEN_DIAG", "").lower() not in {"1", "true", "yes"}:
        return
    try:
        _trace_viewer(viewer, event, gl_state=gl_state)
    except Exception:
        # Diagnostics must never interrupt a frame or an exception cleanup.
        logging.getLogger(__name__).debug("Fullscreen diagnostic unavailable", exc_info=True)


def _trace_viewer(viewer, event: str, *, gl_state: bool) -> None:
    if not viewer._runtime_ready:
        return
    now = time.monotonic()
    state = getattr(viewer, "_fullscreen_diag_state", None)
    if state is None:
        state = {"remaining": 120, "last": {}, "count": 0, "submitted": 0}
        viewer._fullscreen_diag_state = state
        from PySide6 import __version__
        from PySide6.QtCore import qVersion
        from PySide6.QtGui import QGuiApplication

        emit_detail_event(
            "fullscreen_environment",
            generation=0,
            qt=qVersion(),
            pyside=__version__,
            backend=viewer.render_backend_name(),
            screens=[
                {
                    "size": [s.size().width(), s.size().height()],
                    "dpr": s.devicePixelRatio(),
                    "refresh_hz": s.refreshRate(),
                }
                for s in QGuiApplication.screens()
            ],
        )
    if event in {"fit_requested", "relayout_requested", "wheel", "resources_released"}:
        state["remaining"] = 120
    if event == "submitted":
        state["submitted"] += 1
    if event in {"gl_entry", "draw", "submitted"}:
        if state["remaining"] <= 0 and now - state["last"].get(event, 0) < 1.0:
            return
        state["last"][event] = now
        if event == "submitted":
            state["remaining"] -= 1
    state["count"] += 1
    tc = viewer._transform_controller
    pan = tc.get_pan_pixels()
    target = viewer._last_render_target_size
    details = dict(
        event=event,
        sequence=state["count"],
        submissions=state["submitted"],
        viewer_id=id(viewer),
        fullscreen=viewer.window().isFullScreen(),
        widget_size=[viewer.width(), viewer.height()],
        dpr=viewer.devicePixelRatioF(),
        target_size=[target.width(), target.height()],
        texture_size=viewer._display_texture_dimensions(),
        zoom=tc.get_zoom_factor(),
        cover=tc.get_image_cover_scale(),
        effective_scale=tc.get_effective_scale(),
        pan=[pan.x(), pan.y()],
        crop=viewer._logical_crop_values(),
        suppressed=viewer._presentation_suppressed_generation,
        content_revision=viewer._content_revision,
        pending_upload=viewer._texture_manager.needs_texture_upload(),
        reset_pending=viewer._viewport_reset_pending,
    )
    from PySide6.QtCore import Qt

    details["window_translucent"] = viewer.window().testAttribute(
        Qt.WidgetAttribute.WA_TranslucentBackground
    )
    if gl_state:
        try:
            from OpenGL import GL
            from PySide6.QtGui import QOpenGLContext

            context = QOpenGLContext.currentContext()
            if context is not None and not state.get("context_logged"):
                fmt = context.format()
                renderer = GL.glGetString(GL.GL_RENDERER)
                emit_detail_event(
                    "fullscreen_gl_context",
                    generation=0,
                    version=list(fmt.version()),
                    profile=fmt.profile().name,
                    context_reported_swap_interval=fmt.swapInterval(),
                    renderer=renderer.decode("utf-8", errors="replace") if renderer else "unknown",
                )
                state["context_logged"] = True

            details["gl"] = {
                name: bool(GL.glIsEnabled(getattr(GL, "GL_" + name)))
                for name in ("BLEND", "DEPTH_TEST", "STENCIL_TEST", "CULL_FACE", "SCISSOR_TEST")
            }
            for name in ("VIEWPORT", "COLOR_WRITEMASK", "FRAMEBUFFER_BINDING", "CURRENT_PROGRAM"):
                value = GL.glGetIntegerv(getattr(GL, "GL_" + name))
                details["gl"][name] = value.tolist() if hasattr(value, "tolist") else int(value)
            details["gl"]["errors"] = []
            for _ in range(8):
                error = int(GL.glGetError())
                if error == GL.GL_NO_ERROR:
                    break
                details["gl"]["errors"].append(error)
        except Exception as error:
            details["gl_query_error"] = type(error).__name__
    emit_detail_event("fullscreen_trace", generation=0, **details)
