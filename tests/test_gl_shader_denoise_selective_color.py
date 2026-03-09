"""Regression tests for denoise/selective-color interaction in GL shader."""

from pathlib import Path


def _shader_source() -> str:
    root = Path(__file__).resolve().parents[1]
    shader_path = root / "src" / "iPhoto" / "gui" / "ui" / "widgets" / "gl_image_viewer.frag"
    return shader_path.read_text(encoding="utf-8")


def test_denoise_uses_adjusted_color_as_base() -> None:
    """Denoise should augment prior adjustments instead of replacing them."""

    shader = _shader_source()

    assert "vec3 apply_denoise(vec3 adjustedColor, vec2 uv)" in shader
    assert "c = apply_denoise(c, uv_tex);" in shader
    assert "c = apply_denoise(uv_tex);" not in shader
