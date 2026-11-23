"""Regression tests for denoise/selective-color interaction in GL shader."""

import re
from pathlib import Path


def _shader_source() -> str:
    root = Path(__file__).resolve().parents[1]
    shader_path = root / "src" / "iPhoto" / "gui" / "ui" / "widgets" / "gl_image_viewer.frag"
    return shader_path.read_text(encoding="utf-8")


def _rhi_shader_source() -> str:
    root = Path(__file__).resolve().parents[1]
    shader_path = root / "src" / "iPhoto" / "gui" / "ui" / "widgets" / "image_viewer_rhi.frag"
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


def test_texture_origin_y_flip_is_backend_controlled() -> None:
    """Raw GL and QRhi/Metal normalize fragment coordinates before crop math."""

    for shader in (_shader_source(), _rhi_shader_source()):
        normalised = _normalise(shader)

        assert "uTextureOriginTopLeft" in shader
        assert (
            "if (uTextureOriginTopLeft == 0) { fragPx.y = uViewSize.y - 1.0 - fragPx.y; }"
            in normalised
        ), "Fragment coordinates must be normalized to top-left viewport space"
        assert (
            "vec2 worldVector = vec2(fragPx.x - viewCentre.x, viewCentre.y - fragPx.y);"
            in normalised
        ), "Crop math must use the same world-up convention as ViewTransformController"


def test_definition_uses_explicit_mipmap_availability() -> None:
    """RGBA video fallbacks keep LOD Definition while still surfaces use base-level taps."""

    for shader in (_shader_source(), _rhi_shader_source()):
        normalised = _normalise(shader)

        assert "uTextureHasMipmaps" in shader
        assert "if (uTextureHasMipmaps != 0)" in normalised
        blur_start = shader.index("vec3 sample_definition_blur")
        blur_end = shader.index("vec3 apply_definition")
        definition_blur = shader[blur_start:blur_end]
        assert "if (uSourceKind == 1)" not in _normalise(definition_blur)


def test_transformed_source_is_not_clipped_before_inverse_mapping() -> None:
    """Straightened corners survive until they can be mapped to source pixels."""

    for shader in (_shader_source(), _rhi_shader_source()):
        main = shader[shader.index("void main() {") :]
        inverse_mapping = main.index(
            "vec2 uv_perspective = apply_inverse_perspective(uv_corrected);"
        )
        before_inverse = main[:inverse_mapping]
        after_inverse = main[inverse_mapping:]

        assert "uv.x < 0.0 || uv.x > 1.0" not in before_inverse
        assert "uv_perspective.x < 0.0 || uv_perspective.x > 1.0" in after_inverse
