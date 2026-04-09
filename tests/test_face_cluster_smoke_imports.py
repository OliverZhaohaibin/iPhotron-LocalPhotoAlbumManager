from __future__ import annotations

import os
import sys

import pytest


pytest.importorskip("PySide6", reason="PySide6 is required for demo UI imports")

_DEMO_FACE_CLUSTER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "demo", "face-cluster")
)
if _DEMO_FACE_CLUSTER not in sys.path:
    sys.path.insert(0, _DEMO_FACE_CLUSTER)


def test_face_cluster_modules_import() -> None:
    import main  # noqa: F401
    import ui  # noqa: F401
    import worker  # noqa: F401
