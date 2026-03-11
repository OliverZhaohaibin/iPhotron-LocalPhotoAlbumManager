"""Regression tests for denoise/selective-color interaction in GL shader."""

import re
from pathlib import Path


def _shader_source() -> str:
    root = Path(__file__).resolve().parents[1]
    shader_path = root / "src" / "iPhoto" / "gui" / "ui" / "widgets" / "gl_image_viewer.frag"
    return shader_path.read_text(encoding="utf-8")


def _normalise(text: str) -> str:
    """Collapse all whitespace runs to a single space for comparison."""
    return re.sub(r"\s+", " ", text)


def test_denoise_uses_adjusted_color_as_base() -> None:
    """Denoise should augment prior adjustments instead of replacing them."""

    shader = _shader_source()
    normalised = _normalise(shader)

    # The denoise function must accept the already-adjusted colour as its first
    # argument so that it can apply a delta rather than overwrite the pipeline.
    assert re.search(
        r"vec3\s+apply_denoise\s*\(\s*vec3\s+\w+\s*,\s*vec2\s+\w+\s*\)",
        shader,
    ), "apply_denoise must accept (vec3 adjustedColor, vec2 uv)"

    # The main pipeline call must pass the accumulated colour variable as the
    # first argument.
    assert re.search(
        r"c\s*=\s*apply_denoise\s*\(\s*c\s*,\s*\w+\s*\)\s*;",
        normalised,
    ), "apply_denoise must be called with c as the adjusted-colour argument"

    # The old single-argument call site must not be present.
    assert not re.search(
        r"c\s*=\s*apply_denoise\s*\(\s*uv_tex\s*\)\s*;",
        normalised,
    ), "apply_denoise must not be called with only uv_tex (old single-arg form)"
