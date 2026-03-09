"""Verify that noise reduction and selective color adjustments coexist.

Regression test for the bug where activating noise reduction in the GPU
fragment shader would reset (override) selective-color adjustments because
``apply_denoise`` sampled directly from the original texture, discarding all
prior colour processing.
"""

import os
import re

import pytest


# ---------------------------------------------------------------------------
# 1.  Shader structure: denoise must run BEFORE selective-color processing
# ---------------------------------------------------------------------------

def _load_fragment_shader() -> str:
    """Return the raw text of the GL fragment shader."""
    this_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(this_dir)
    frag_path = os.path.join(
        project_root, "src", "iPhoto", "gui", "ui", "widgets",
        "gl_image_viewer.frag",
    )
    with open(frag_path) as fh:
        return fh.read()


def test_shader_applies_denoise_before_selective_color():
    """``apply_denoise`` must execute before ``apply_selective_color`` in main().

    Because the bilateral filter samples neighbouring texels from the raw
    texture, it cannot run after colour adjustments without overriding them.
    Moving denoise to the start of the pipeline ensures selective-color (and
    all other adjustments) are preserved.
    """
    source = _load_fragment_shader()

    # Extract the main() body
    main_match = re.search(r"void\s+main\s*\(\s*\)\s*\{", source)
    assert main_match is not None, "Could not find main() in fragment shader"
    main_start = main_match.start()

    # Find the positions of denoise and selective color calls within main()
    main_body = source[main_start:]

    denoise_call = re.search(r"\bapply_denoise\b", main_body)
    sc_call = re.search(r"\bapply_selective_color\b", main_body)

    assert denoise_call is not None, "apply_denoise not found in main()"
    assert sc_call is not None, "apply_selective_color not found in main()"

    assert denoise_call.start() < sc_call.start(), (
        "apply_denoise must appear before apply_selective_color in main() "
        "so that the bilateral filter operates on raw texture data and does "
        "not discard selective-color adjustments"
    )


def test_shader_denoise_does_not_override_after_selective_color():
    """There must be no ``apply_denoise`` call after ``apply_selective_color``.

    A second ``apply_denoise`` after selective-color would re-introduce the
    bug by sampling from the raw texture and discarding colour adjustments.
    """
    source = _load_fragment_shader()

    main_match = re.search(r"void\s+main\s*\(\s*\)\s*\{", source)
    assert main_match is not None
    main_body = source[main_match.start():]

    sc_match = re.search(r"\bapply_selective_color\b", main_body)
    assert sc_match is not None

    after_sc = main_body[sc_match.end():]
    # Only match actual function *calls* (with parentheses), not comments
    late_denoise = re.search(r"\bapply_denoise\s*\(", after_sc)
    assert late_denoise is None, (
        "apply_denoise must not be called after apply_selective_color; "
        "the bilateral filter would sample from the raw texture and discard "
        "selective-color adjustments"
    )


# ---------------------------------------------------------------------------
# 2.  Session model: denoise writes must not touch selective-color keys
# ---------------------------------------------------------------------------

@pytest.fixture()
def _ensure_qapp():
    """Provide a QApplication for EditSession (which is a QObject)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication  # noqa: E402
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_session_denoise_preserves_selective_color(_ensure_qapp):
    """Committing denoise values must leave selective-color values intact."""
    from iPhoto.gui.ui.models.edit_session import EditSession

    session = EditSession()

    # Set selective-color adjustments
    custom_ranges = session.value("SelectiveColor_Ranges")
    custom_ranges[0] = [0.0, 0.5, 0.3, -0.2, 0.1]
    session.set_values({
        "SelectiveColor_Enabled": True,
        "SelectiveColor_Ranges": custom_ranges,
    })

    # Now commit denoise adjustments
    session.set_values({
        "Denoise_Enabled": True,
        "Denoise_Amount": 2.5,
    })

    # Selective-color values must be unchanged
    assert session.value("SelectiveColor_Enabled") is True
    ranges = session.value("SelectiveColor_Ranges")
    assert ranges[0] == [0.0, 0.5, 0.3, -0.2, 0.1]


def test_resolved_adjustments_include_both(_ensure_qapp):
    """The resolver must emit both denoise and selective-color parameters."""
    from iPhoto.gui.ui.models.edit_session import EditSession
    from iPhoto.gui.ui.controllers.edit_preview_manager import (
        resolve_adjustment_mapping,
    )

    session = EditSession()

    custom_ranges = session.value("SelectiveColor_Ranges")
    custom_ranges[0] = [0.0, 0.5, 0.3, -0.2, 0.1]
    session.set_values({
        "SelectiveColor_Enabled": True,
        "SelectiveColor_Ranges": custom_ranges,
        "Denoise_Enabled": True,
        "Denoise_Amount": 2.5,
    })

    resolved = resolve_adjustment_mapping(session.values())

    assert resolved["SelectiveColor_Enabled"] == 1.0
    assert "SelectiveColor_Ranges" in resolved
    assert resolved["SelectiveColor_Ranges"][0] == [0.0, 0.5, 0.3, -0.2, 0.1]
    assert resolved["Denoise_Enabled"] == 1.0
    assert resolved["Denoise_Amount"] == 2.5
