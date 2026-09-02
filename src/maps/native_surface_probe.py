"""One-shot inspection of legacy native Qt surfaces, outside the media process."""

from __future__ import annotations

import ctypes
import json
import os
import sys


def main(arguments: list[str]) -> int:
    # Dispatched before the GUI bootstrap, including from a frozen application.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from pathlib import Path

    import shiboken6
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from maps.map_widget.native_osmand_widget import _load_bridge

    config = json.loads(arguments[0])
    _app = QApplication([])
    bridge = _load_bridge(Path(config["library"]))
    error = ctypes.create_unicode_buffer(4096)
    pointer = bridge.library.osmand_create_map_widget(
        None,
        config["obf"],
        config["resources"],
        config["style"],
        0,
        ctypes.cast(error, ctypes.c_void_p),
        len(error),
    )
    if not pointer:
        print(error.value, file=sys.stderr, flush=True)
        return 1
    host = shiboken6.wrapInstance(int(pointer), QObject)
    get_target = getattr(bridge.library, "osmand_widget_get_event_target", None)
    target_pointer = get_target(pointer) if get_target is not None else pointer
    target = shiboken6.wrapInstance(int(target_pointer or pointer), QObject)
    # An old host may already contain an independent QOpenGLWindow. A missing
    # version-query symbol says nothing about its composition requirements.
    has_gl_widget = host.inherits("QOpenGLWidget") or any(
        child.inherits("QOpenGLWidget") for child in host.findChildren(QObject)
    )
    kind = "unknown"
    if has_gl_widget:
        kind = "opengl_widget"
    elif target.inherits("QOpenGLWindow"):
        kind = "opengl_window"
    print(f"NATIVE_SURFACE={kind}", flush=True)
    # Old extensions do not all export cleanup, and Qt/native finalizers can
    # outlive one another. The child owns no user data and never shows a window.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
