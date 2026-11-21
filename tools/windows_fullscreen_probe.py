#!/usr/bin/env python3
"""Exercise the real Windows compositor with synthetic media, never user files.

This isolates the production viewer in a translucent frameless Qt window. The
application's actual window-manager/Detail flows still require the collector's
manual matrix. Screen grabs, unlike grabFramebuffer(), do not force a new draw.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path


def capture_visible_regions(viewer_rect, screens):
    """Capture each visible desktop region in that screen's own coordinate space.

    Windows screen.grabWindow(0, ...) uses screen-local logical coordinates.
    Capturing a translucent HWND is unsupported. Never sample outside a screen
    or infer capture scale from the size of a window spanning multiple screens.
    """
    captures = []
    for index, screen in enumerate(screens):
        screen_rect = screen.geometry()
        visible = viewer_rect.intersected(screen_rect)
        if visible.isEmpty():
            continue
        local = visible.translated(-screen_rect.topLeft())
        record = {
            "capture_method": "desktop_region",
            "screen_index": index,
            "screen_rect_logical": list(screen_rect.getRect()),
            "capture_rect_global": list(visible.getRect()),
            "capture_rect_screen_local": list(local.getRect()),
            "ok": False,
            "status": "capture_failed",
        }
        image = None
        try:
            pixmap = screen.grabWindow(0, local.x(), local.y(), local.width(), local.height())
            if pixmap.isNull():
                record["reason"] = "empty_capture"
            else:
                dpr = float(pixmap.devicePixelRatio())
                image = pixmap.toImage()
                record.update(
                    dpr=dpr if math.isfinite(dpr) else None,
                    pixel_size=[image.width(), image.height()],
                )
                if not math.isfinite(dpr) or dpr <= 0:
                    record["reason"] = "invalid_capture_dpr"
                elif (
                    image.isNull()
                    or abs(image.width() - visible.width() * dpr) > 1
                    or abs(image.height() - visible.height() * dpr) > 1
                ):
                    record["reason"] = "capture_size_mismatch"
                else:
                    record["status"] = "captured"
        except Exception as error:
            record["reason"] = type(error).__name__
        captures.append((record, image))
    if not captures:
        captures.append(
            (
                {
                    "capture_method": "desktop_region",
                    "ok": False,
                    "status": "no_visible_intersection",
                },
                None,
            )
        )
    return captures


def evaluate_capture(record, image, expected_global_rect):
    """Check the synthetic green rectangle using capture pixels, not window DPR."""
    from PySide6.QtCore import QRectF

    result = dict(record)
    if record["status"] != "captured":
        return result
    if expected_global_rect.isEmpty():
        result.update(status="invalid_expected_geometry", ok=False)
        return result
    x, y, width, height = record["capture_rect_global"]
    dpr = record["dpr"]
    visible = expected_global_rect.intersected(QRectF(x, y, width, height))
    expected = QRectF(
        (visible.x() - x) * dpr,
        (visible.y() - y) * dpr,
        visible.width() * dpr,
        visible.height() * dpr,
    )
    expected = expected.intersected(QRectF(0, 0, image.width(), image.height()))
    result["expected_bounds_px"] = list(expected.getRect())

    def green(px, py):
        color = image.pixelColor(px, py)
        return (
            color.green() > 100
            and color.green() > color.red() * 1.5
            and color.green() > color.blue() * 1.5
        )

    result["status"] = "pixel_mismatch"
    if visible.isEmpty():
        # A clipped region can contain only the viewer's opaque black backdrop.
        points = [
            (
                min(image.width() - 1, int(image.width() * fx)),
                min(image.height() - 1, int(image.height() * fy)),
            )
            for fx in (0.1, 0.5, 0.9)
            for fy in (0.1, 0.5, 0.9)
        ]
        result["ok"] = all(max(image.pixelColor(px, py).getRgb()[:3]) <= 24 for px, py in points)
        result["reason"] = "background_only"
    else:
        cx = max(0, min(image.width() - 1, int(expected.center().x())))
        cy = max(0, min(image.height() - 1, int(expected.center().y())))
        xs = [px for px in range(image.width()) if green(px, cy)]
        ys = [py for py in range(image.height()) if green(cx, py)]
        if not xs or not ys:
            result["reason"] = "missing_green_content"
        else:
            observed = QRectF(min(xs), min(ys), max(xs) + 1 - min(xs), max(ys) + 1 - min(ys))
            error = max(
                abs(a - b)
                for a, b in zip(
                    (observed.left(), observed.top(), observed.right(), observed.bottom()),
                    (expected.left(), expected.top(), expected.right(), expected.bottom()),
                    strict=True,
                )
            )
            result.update(
                observed_bounds_px=list(observed.getRect()), bounds_error_px=error, ok=error <= 3
            )
    if result["ok"]:
        result["status"] = "passed"
    return result


def probe_surface_format(swap_interval: int):
    """Match the application's compatibility-profile request for WGL A/B runs."""
    from PySide6.QtGui import QSurfaceFormat

    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    fmt.setAlphaBufferSize(8)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setSamples(0)
    fmt.setSwapInterval(swap_interval)
    return fmt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("fullscreen-probe"))
    parser.add_argument("--poison-gl-state", action="store_true")
    composition_options = parser.add_mutually_exclusive_group()
    composition_options.add_argument(
        "--fullscreen-border",
        action="store_true",
        help="Try Qt's Windows OpenGL WS_BORDER composition workaround",
    )
    composition_options.add_argument(
        "--fullscreen-overscan",
        action="store_true",
        help="Extend fullscreen height by one logical pixel while retaining the Qt window",
    )
    parser.add_argument(
        "--opaque-window",
        action="store_true",
        help="Disable top-level translucency for a separate compositor A/B run",
    )
    parser.add_argument(
        "--swap-interval",
        type=int,
        choices=(0, 1),
        default=1,
        help="Requested WGL vsync interval; drivers may ignore it",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("requires an interactive Windows desktop")
    if not 1 <= args.cycles <= 100:
        parser.error("--cycles must be between 1 and 100")
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["QT_QPA_PLATFORM"] = "windows"
    os.environ["IPHOTO_RHI_BACKEND"] = "opengl"
    os.environ["IPHOTO_FULLSCREEN_DIAG"] = "1"
    os.environ["IPHOTO_WINDOWS_FULLSCREEN_BORDER"] = "1" if args.fullscreen_border else "0"
    os.environ["IPHOTO_WINDOWS_FULLSCREEN_OVERSCAN"] = "1" if args.fullscreen_overscan else "0"
    os.environ["IPHOTO_DETAIL_PROFILE"] = "1"
    os.environ["IPHOTO_DETAIL_PROFILE_PATH"] = str(args.output / "detail_events.jsonl")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QSurfaceFormat, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

    from iPhoto.gui.detail_profile import shutdown_detail_profile
    from iPhoto.gui.windowed_fullscreen import (
        FullscreenPhase,
        enter_media_fullscreen,
        exit_media_fullscreen,
    )
    from iPhoto.gui.ui.widgets.gl_image_viewer import GLImageViewer
    from iPhoto.gui.windows_fullscreen_composition import install_fullscreen_composition_guard

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL)
    QSurfaceFormat.setDefaultFormat(probe_surface_format(args.swap_interval))
    _app = QApplication([])
    host = QMainWindow()
    host.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
    host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, not args.opaque_window)
    composition_guard = install_fullscreen_composition_guard(host)
    shell = QWidget(host)
    shell.setStyleSheet("background: black")
    layout = QVBoxLayout(shell)
    layout.setContentsMargins(0, 0, 0, 0)
    header = QLabel("Synthetic fullscreen probe — keep this window unobscured")
    layout.addWidget(header)
    viewer = GLImageViewer(shell)
    viewer.set_immersive_background(True)
    layout.addWidget(viewer, 1)
    host.setCentralWidget(shell)
    host.resize(900, 650)
    submissions = [0]
    viewer.frameSubmitted.connect(lambda: submissions.__setitem__(0, submissions[0] + 1))
    failures = []
    samples = []

    def wait_for_frame(before):
        deadline = time.monotonic() + 5
        while submissions[0] <= before and time.monotonic() < deadline:
            QTest.qWait(20)
        if submissions[0] <= before:
            raise RuntimeError("No QRhi submission within 5 seconds")
        QTest.qWait(200)

    def sample(label):
        from PySide6.QtCore import QRect, QRectF

        origin = viewer.mapToGlobal(QPoint(0, 0))
        viewport = QRect(origin, viewer.size())
        crop = viewer._compute_crop_rect_pixels()
        if crop is None:
            width, height = viewer._display_texture_dimensions()
            crop = QRectF(0, 0, width, height)
        transform = viewer._transform_controller
        left = transform.convert_image_to_viewport(crop.left(), crop.top()) + QPointF(origin)
        right = transform.convert_image_to_viewport(crop.right(), crop.bottom()) + QPointF(origin)
        expected = QRectF(left, right).normalized()
        regions = []
        for region_index, (record, image) in enumerate(
            capture_visible_regions(viewport, _app.screens())
        ):
            result = evaluate_capture(record, image, expected)
            regions.append(result)
            if not result["ok"]:
                failure = {
                    "stage": label,
                    "region": region_index,
                    "status": result["status"],
                    "reason": result.get("reason"),
                }
                if image is not None and not image.isNull():
                    name = f"failure-{len(failures):03d}.png"
                    image.save(str(args.output / name))
                    failure["screenshot"] = name
                failures.append(failure)
        samples.append(
            {
                "stage": label,
                "ok": all(region["ok"] for region in regions),
                "capture_method": "desktop_region",
                "submissions": submissions[0],
                "window_active": host.isActiveWindow(),
                "window_state": host.windowState().value,
                "window_geometry": list(host.geometry().getRect()),
                "monotonic_ms": time.monotonic() * 1000,
                "regions": regions,
            }
        )

    try:
        before = submissions[0]
        host.show()
        wait_for_frame(before)
        if args.poison_gl_state:
            from OpenGL import GL

            prepare = viewer._renderer.prepare_draw_state

            def poisoned(width, height):
                for flag in (
                    GL.GL_BLEND,
                    GL.GL_DEPTH_TEST,
                    GL.GL_STENCIL_TEST,
                    GL.GL_CULL_FACE,
                    GL.GL_SCISSOR_TEST,
                ):
                    GL.glEnable(flag)
                GL.glScissor(0, 0, 1, 1)
                GL.glColorMask(False, False, False, False)
                prepare(width, height)

            viewer._renderer.prepare_draw_state = poisoned
        variants = [
            ({}, "plain"),
            ({"Crop_CX": 0.6, "Crop_CY": 0.5, "Crop_W": 0.45, "Crop_H": 0.4}, "crop"),
            (
                {
                    "Crop_CX": 0.6,
                    "Crop_CY": 0.5,
                    "Crop_W": 0.45,
                    "Crop_H": 0.4,
                    "Crop_Straighten": 15.0,
                },
                "straighten",
            ),
        ]
        for adjustments, name in variants:
            image = QImage(1600, 1200, QImage.Format.Format_RGBA8888)
            image.fill(QColor(30, 220, 50))
            before = submissions[0]
            viewer.set_image(image, adjustments, image_source=name)
            viewer.request_viewport_relayout(reset_view=True)
            wait_for_frame(before)
            for cycle in range(args.cycles):
                for fullscreen in (True, False):
                    before = submissions[0]
                    header.setVisible(not fullscreen)
                    enter_media_fullscreen(host) if fullscreen else exit_media_fullscreen(host)
                    if composition_guard is not None:
                        composition_guard.apply_if_fullscreen()
                    viewer.request_viewport_relayout(reset_view=True)
                    wait_for_frame(before)
                    stage = f"{name}/{cycle}/{'full' if fullscreen else 'window'}"
                    if fullscreen and args.fullscreen_overscan:
                        controller = getattr(host, "_iphoto_fullscreen_controller", None)
                        if controller is None or controller.phase != FullscreenPhase.WINDOWED:
                            failures.append(
                                {
                                    "stage": stage,
                                    "error": "overscan_not_active",
                                    "phase": controller.phase.value if controller else None,
                                }
                            )
                    for index in range(5):
                        sample(f"{stage}/idle-{index}")
                        QTest.qWait(60)
                    for delta in (-120, 120):
                        before = submissions[0]
                        pos = viewer.viewport_center()
                        wheel = QWheelEvent(
                            pos,
                            QPointF(viewer.mapToGlobal(pos.toPoint())),
                            QPoint(),
                            QPoint(0, delta),
                            Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase,
                            False,
                        )
                        QApplication.sendEvent(viewer, wheel)
                        wait_for_frame(before)
                        sample(f"{stage}/wheel-{delta}")
                print(f"{name}: {cycle + 1}/{args.cycles}, failures={len(failures)}", flush=True)
    except Exception as error:
        failures.append({"error": str(error)})
    finally:
        verified_controller = (
            getattr(host, "_iphoto_fullscreen_controller", None)
            if args.fullscreen_overscan
            else composition_guard
        )
        if (args.fullscreen_border or args.fullscreen_overscan) and (
            verified_controller is None or verified_controller.verification_count == 0
        ):
            failures.append({"error": "Fullscreen composition candidate was never verified"})
        host.close()
        # The probe owns the only application instance and the diagnostic writer.
        shutdown_detail_profile()
        (args.output / "result.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "capture_method": "desktop_region",
                    "passed": not failures,
                    "cycles": args.cycles,
                    "poison_gl_state": args.poison_gl_state,
                    "opaque_window": args.opaque_window,
                    "requested_swap_interval": args.swap_interval,
                    "fullscreen_border": args.fullscreen_border,
                    "fullscreen_overscan": args.fullscreen_overscan,
                    "fullscreen_composition_verifications": (
                        verified_controller.verification_count
                        if verified_controller is not None
                        else 0
                    ),
                    "fullscreen_border_verifications": (
                        composition_guard.verification_count if composition_guard is not None else 0
                    ),
                    "failures": failures,
                    "samples": samples,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    print(f"Results: {args.output.resolve()}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
