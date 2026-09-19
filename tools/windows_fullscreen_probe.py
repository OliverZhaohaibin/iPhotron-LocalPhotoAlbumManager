#!/usr/bin/env python3
"""Exercise the real Windows compositor with synthetic media, never user files.

This isolates the production viewer in a translucent frameless Qt window. The
application's actual window-manager/Detail flows still require the collector's
manual matrix. Screen grabs, unlike grabFramebuffer(), do not force a new draw.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


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
    parser.add_argument(
        "--fullscreen-border",
        action="store_true",
        help="Try Qt's Windows OpenGL WS_BORDER composition workaround",
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
    os.environ["IPHOTO_DETAIL_PROFILE"] = "1"
    os.environ["IPHOTO_DETAIL_PROFILE_PATH"] = str(args.output / "detail_events.jsonl")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QSurfaceFormat, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

    from iPhoto.gui.detail_profile import shutdown_detail_profile
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

    def sample(label, *, check_bounds=True):
        screen = host.screen()
        image = screen.grabWindow(int(host.winId())).toImage()
        if image.isNull():
            raise RuntimeError("Windows window capture returned no pixels")
        sx, sy = image.width() / host.width(), image.height() / host.height()
        origin = viewer.mapTo(host, QPoint(0, 0))
        cx = round((origin.x() + viewer.width() / 2) * sx)
        cy = round((origin.y() + viewer.height() / 2) * sy)

        def green(x, y):
            c = image.pixelColor(x, y)
            return c.green() > 100 and c.green() > c.red() * 1.5 and c.green() > c.blue() * 1.5

        ok = green(cx, cy)
        error = None
        if check_bounds and ok:
            xs = [x for x in range(image.width()) if green(x, cy)]
            ys = [y for y in range(image.height()) if green(cx, y)]
            crop = viewer._compute_crop_rect_pixels()
            if crop is None:
                from PySide6.QtCore import QRectF

                w, h = viewer._display_texture_dimensions()
                crop = QRectF(0, 0, w, h)
            tc = viewer._transform_controller
            left = tc.convert_image_to_viewport(crop.left(), crop.top())
            right = tc.convert_image_to_viewport(crop.right(), crop.bottom())
            expected = [
                (origin.x() + left.x()) * sx,
                (origin.x() + right.x()) * sx,
                (origin.y() + left.y()) * sy,
                (origin.y() + right.y()) * sy,
            ]
            actual = [min(xs), max(xs) + 1, min(ys), max(ys) + 1]
            error = max(abs(a - b) for a, b in zip(actual, expected, strict=True))
            ok = error <= 3
        samples.append(
            {"stage": label, "ok": ok, "bounds_error_px": error, "submissions": submissions[0]}
        )
        if not ok:
            name = f"failure-{len(failures):03d}.png"
            image.save(str(args.output / name))
            failures.append({"stage": label, "screenshot": name})

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
                    host.showFullScreen() if fullscreen else host.showNormal()
                    if composition_guard is not None:
                        composition_guard.apply_if_fullscreen()
                    viewer.request_viewport_relayout(reset_view=True)
                    wait_for_frame(before)
                    stage = f"{name}/{cycle}/{'full' if fullscreen else 'window'}"
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
        if args.fullscreen_border and (
            composition_guard is None or composition_guard.verification_count == 0
        ):
            failures.append({"error": "Fullscreen border was requested but never verified on HWND"})
        host.close()
        # The probe owns the only application instance and the diagnostic writer.
        shutdown_detail_profile()
        (args.output / "result.json").write_text(
            json.dumps(
                {
                    "passed": not failures,
                    "cycles": args.cycles,
                    "poison_gl_state": args.poison_gl_state,
                    "opaque_window": args.opaque_window,
                    "requested_swap_interval": args.swap_interval,
                    "fullscreen_border": args.fullscreen_border,
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
