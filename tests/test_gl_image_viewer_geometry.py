"""
Unit tests for gl_image_viewer geometry transformations.

Tests the coordinate transformation logic between texture space and logical space,
particularly for rotation operations.

Note: We import the geometry module directly rather than through the package
to avoid Qt dependencies that are triggered by the package __init__.py.
This allows testing pure Python logic without requiring Qt libraries.
"""

import sys
from pathlib import Path

import pytest

# Direct module import to avoid Qt dependencies in test environment
geometry_path = (
    Path(__file__).parent.parent
    / "src"
    / "iPhoto"
    / "gui"
    / "ui"
    / "widgets"
    / "gl_image_viewer"
)
sys.path.insert(0, str(geometry_path))

import geometry  # noqa: E402

clamp_unit = geometry.clamp_unit
get_rotate_steps = geometry.get_rotate_steps
logical_crop_from_texture = geometry.logical_crop_from_texture
logical_crop_mapping_from_texture = geometry.logical_crop_mapping_from_texture
logical_crop_to_texture = geometry.logical_crop_to_texture
normalised_crop_from_mapping = geometry.normalised_crop_from_mapping
texture_crop_to_logical = geometry.texture_crop_to_logical


class TestClampUnit:
    """Test the clamp_unit function."""

    def test_clamp_negative(self):
        """Values below 0 should be clamped to 0."""
        assert clamp_unit(-0.5) == 0.0

    def test_clamp_above_one(self):
        """Values above 1 should be clamped to 1."""
        assert clamp_unit(1.5) == 1.0

    def test_clamp_within_range(self):
        """Values within [0, 1] should remain unchanged."""
        assert clamp_unit(0.5) == 0.5
        assert clamp_unit(0.0) == 0.0
        assert clamp_unit(1.0) == 1.0


class TestGetRotateSteps:
    """Test rotation step extraction."""

    def test_no_rotation(self):
        """Default or zero rotation should return 0."""
        assert get_rotate_steps({}) == 0
        assert get_rotate_steps({"Crop_Rotate90": 0.0}) == 0

    def test_rotation_steps(self):
        """Various rotation steps should be normalized to 0-3."""
        assert get_rotate_steps({"Crop_Rotate90": 1.0}) == 1
        assert get_rotate_steps({"Crop_Rotate90": 2.0}) == 2
        assert get_rotate_steps({"Crop_Rotate90": 3.0}) == 3
        assert get_rotate_steps({"Crop_Rotate90": 4.0}) == 0  # wraps around
        assert get_rotate_steps({"Crop_Rotate90": 5.0}) == 1


class TestNormalisedCropFromMapping:
    """Test extraction of normalized crop values."""

    def test_default_values(self):
        """Empty mapping should return default centered full crop."""
        cx, cy, w, h = normalised_crop_from_mapping({})
        assert cx == 0.5
        assert cy == 0.5
        assert w == 1.0
        assert h == 1.0

    def test_custom_values(self):
        """Custom crop values should be extracted and clamped."""
        values = {
            "Crop_CX": 0.3,
            "Crop_CY": 0.7,
            "Crop_W": 0.5,
            "Crop_H": 0.6,
        }
        cx, cy, w, h = normalised_crop_from_mapping(values)
        assert cx == 0.3
        assert cy == 0.7
        assert w == 0.5
        assert h == 0.6

    def test_out_of_range_clamping(self):
        """Out-of-range values should be clamped."""
        values = {
            "Crop_CX": -0.1,
            "Crop_CY": 1.5,
            "Crop_W": 2.0,
            "Crop_H": -0.5,
        }
        cx, cy, w, h = normalised_crop_from_mapping(values)
        assert cx == 0.0
        assert cy == 1.0
        assert w == 1.0
        assert h == 0.0


class TestTextureCropToLogical:
    """Test texture-to-logical crop coordinate transformation."""

    def test_no_rotation(self):
        """Zero rotation should preserve coordinates."""
        crop = (0.3, 0.7, 0.5, 0.6)
        result = texture_crop_to_logical(crop, 0)
        assert result == crop

    def test_90_degree_rotation(self):
        """90° CW rotation should transform coordinates correctly."""
        crop = (0.3, 0.7, 0.5, 0.6)
        result = texture_crop_to_logical(crop, 1)
        # Step 1: (x', y') = (1-y, x) and swap w/h
        expected_x = 1.0 - 0.7  # 0.3
        expected_y = 0.3
        expected_w = 0.6  # height becomes width
        expected_h = 0.5  # width becomes height
        assert result[0] == pytest.approx(expected_x)
        assert result[1] == pytest.approx(expected_y)
        assert result[2] == pytest.approx(expected_w)
        assert result[3] == pytest.approx(expected_h)

    def test_180_degree_rotation(self):
        """180° rotation should invert both coordinates."""
        crop = (0.3, 0.7, 0.5, 0.6)
        result = texture_crop_to_logical(crop, 2)
        expected = (1.0 - 0.3, 1.0 - 0.7, 0.5, 0.6)
        assert result[0] == pytest.approx(expected[0])
        assert result[1] == pytest.approx(expected[1])
        assert result[2] == pytest.approx(expected[2])
        assert result[3] == pytest.approx(expected[3])

    def test_270_degree_rotation(self):
        """270° CW (90° CCW) rotation should transform correctly."""
        crop = (0.3, 0.7, 0.5, 0.6)
        result = texture_crop_to_logical(crop, 3)
        # Step 3: (x', y') = (y, 1-x) and swap w/h
        expected_x = 0.7
        expected_y = 1.0 - 0.3  # 0.7
        expected_w = 0.6
        expected_h = 0.5
        assert result[0] == pytest.approx(expected_x)
        assert result[1] == pytest.approx(expected_y)
        assert result[2] == pytest.approx(expected_w)
        assert result[3] == pytest.approx(expected_h)


class TestLogicalCropToTexture:
    """Test logical-to-texture crop coordinate transformation (inverse)."""

    def test_no_rotation_inverse(self):
        """Zero rotation should preserve coordinates."""
        crop = (0.3, 0.7, 0.5, 0.6)
        result = logical_crop_to_texture(crop, 0)
        assert result == crop

    def test_rotation_inverse_property(self):
        """Converting texture -> logical -> texture should preserve original."""
        original = (0.3, 0.7, 0.5, 0.6)
        
        for rotate_steps in range(4):
            logical = texture_crop_to_logical(original, rotate_steps)
            back_to_texture = logical_crop_to_texture(logical, rotate_steps)
            
            assert back_to_texture[0] == pytest.approx(original[0], abs=1e-6)
            assert back_to_texture[1] == pytest.approx(original[1], abs=1e-6)
            assert back_to_texture[2] == pytest.approx(original[2], abs=1e-6)
            assert back_to_texture[3] == pytest.approx(original[3], abs=1e-6)


class TestLogicalCropFromTexture:
    """Test complete transformation from texture mapping to logical coordinates."""

    def test_with_rotation(self):
        """Should extract and transform crop values correctly."""
        values = {
            "Crop_CX": 0.3,
            "Crop_CY": 0.7,
            "Crop_W": 0.5,
            "Crop_H": 0.6,
            "Crop_Rotate90": 1.0,  # 90° rotation
        }
        cx, cy, w, h = logical_crop_from_texture(values)
        
        # Should be same as texture_crop_to_logical((0.3, 0.7, 0.5, 0.6), 1)
        expected_x = 1.0 - 0.7
        expected_y = 0.3
        expected_w = 0.6
        expected_h = 0.5
        
        assert cx == pytest.approx(expected_x)
        assert cy == pytest.approx(expected_y)
        assert w == pytest.approx(expected_w)
        assert h == pytest.approx(expected_h)


class TestLogicalCropMappingFromTexture:
    """Test conversion to mapping dictionary."""

    def test_returns_mapping(self):
        """Should return a dictionary with correct keys."""
        values = {
            "Crop_CX": 0.3,
            "Crop_CY": 0.7,
            "Crop_W": 0.5,
            "Crop_H": 0.6,
            "Crop_Rotate90": 0.0,
        }
        result = logical_crop_mapping_from_texture(values)
        
        assert isinstance(result, dict)
        assert "Crop_CX" in result
        assert "Crop_CY" in result
        assert "Crop_W" in result
        assert "Crop_H" in result
        
        # With no rotation, values should match
        assert result["Crop_CX"] == 0.3
        assert result["Crop_CY"] == 0.7
        assert result["Crop_W"] == 0.5
        assert result["Crop_H"] == 0.6


texture_point_to_logical = geometry.texture_point_to_logical
texture_rect_to_logical = geometry.texture_rect_to_logical


class TestTexturePointToLogical:
    """Tests for texture_point_to_logical helper (rotation + flip + clamping)."""

    def test_no_rotation_identity(self):
        """Without rotation the point maps straight to pixel coordinates."""
        lx, ly = texture_point_to_logical(
            100.0, 80.0, texture_width=400.0, texture_height=300.0, rotate_steps=0
        )
        assert lx == pytest.approx(100.0)
        assert ly == pytest.approx(80.0)

    def test_rotate_90_cw(self):
        """Clockwise 90° (rotate_steps=1): point (x, y) → (H-y, x) in logical pixels."""
        # For a 400×300 texture, rotate_steps=1 yields a 300×400 logical canvas.
        lx, ly = texture_point_to_logical(
            100.0, 60.0, texture_width=400.0, texture_height=300.0, rotate_steps=1
        )
        # nx=0.25, ny=0.2 → lx=1-0.2=0.8, ly=0.25; logical 300×400 → (240, 100)
        assert lx == pytest.approx(0.8 * 300.0)
        assert ly == pytest.approx(0.25 * 400.0)

    def test_rotate_180(self):
        """180° rotation (rotate_steps=2): point (x,y) → (W-x, H-y) in logical pixels."""
        lx, ly = texture_point_to_logical(
            100.0, 60.0, texture_width=400.0, texture_height=300.0, rotate_steps=2
        )
        # nx=0.25, ny=0.2 → lx=0.75, ly=0.8; logical 400×300 → (300, 240)
        assert lx == pytest.approx(0.75 * 400.0)
        assert ly == pytest.approx(0.8 * 300.0)

    def test_rotate_270_cw(self):
        """Clockwise 270° (rotate_steps=3): point (x,y) → (y, W-x) in logical pixels."""
        lx, ly = texture_point_to_logical(
            100.0, 60.0, texture_width=400.0, texture_height=300.0, rotate_steps=3
        )
        # nx=0.25, ny=0.2 → lx=ny=0.2, ly=1-nx=0.75; logical 300×400 → (60, 300)
        assert lx == pytest.approx(0.2 * 300.0)
        assert ly == pytest.approx(0.75 * 400.0)

    def test_flip_horizontal_no_rotation(self):
        """Horizontal flip (no rotation) mirrors lx around the centre."""
        lx, ly = texture_point_to_logical(
            100.0, 80.0,
            texture_width=400.0,
            texture_height=300.0,
            rotate_steps=0,
            flip_horizontal=True,
        )
        # nx=0.25 → lx=1-0.25=0.75 → 300.0
        assert lx == pytest.approx(300.0)
        assert ly == pytest.approx(80.0)

    def test_flip_horizontal_with_rotate_90(self):
        """Flip after rotate_steps=1 mirrors lx of the already-rotated frame."""
        lx_no_flip, _ = texture_point_to_logical(
            100.0, 60.0, texture_width=400.0, texture_height=300.0, rotate_steps=1
        )
        lx_flip, ly_flip = texture_point_to_logical(
            100.0, 60.0,
            texture_width=400.0,
            texture_height=300.0,
            rotate_steps=1,
            flip_horizontal=True,
        )
        assert lx_flip == pytest.approx(300.0 - lx_no_flip)

    def test_invalid_texture_dimensions_returns_zero(self):
        """Zero or negative texture dimensions must return (0, 0)."""
        assert texture_point_to_logical(50.0, 50.0, texture_width=0.0, texture_height=100.0, rotate_steps=0) == (0.0, 0.0)
        assert texture_point_to_logical(50.0, 50.0, texture_width=100.0, texture_height=0.0, rotate_steps=0) == (0.0, 0.0)
        assert texture_point_to_logical(50.0, 50.0, texture_width=-1.0, texture_height=100.0, rotate_steps=0) == (0.0, 0.0)

    def test_clamping_beyond_texture_boundary(self):
        """Points outside the texture boundary are clamped to [0, logical_size]."""
        lx, ly = texture_point_to_logical(
            500.0, 400.0, texture_width=400.0, texture_height=300.0, rotate_steps=0
        )
        assert lx == pytest.approx(400.0)
        assert ly == pytest.approx(300.0)

    def test_clamping_negative_coords(self):
        """Negative coordinates clamp to 0."""
        lx, ly = texture_point_to_logical(
            -10.0, -5.0, texture_width=400.0, texture_height=300.0, rotate_steps=0
        )
        assert lx == pytest.approx(0.0)
        assert ly == pytest.approx(0.0)

    def test_unknown_rotate_steps_treated_as_zero(self):
        """rotate_steps values other than 1/2/3 fall through to identity."""
        lx, ly = texture_point_to_logical(
            100.0, 80.0, texture_width=400.0, texture_height=300.0, rotate_steps=4
        )
        assert lx == pytest.approx(100.0)
        assert ly == pytest.approx(80.0)


class TestTextureRectToLogical:
    """Tests for texture_rect_to_logical helper."""

    def test_no_rotation_passthrough(self):
        """Without rotation the rect dimensions are preserved."""
        x, y, w, h = texture_rect_to_logical(
            40.0, 30.0, 80.0, 60.0,
            texture_width=400.0,
            texture_height=300.0,
            rotate_steps=0,
        )
        assert x == pytest.approx(40.0)
        assert y == pytest.approx(30.0)
        assert w == pytest.approx(80.0)
        assert h == pytest.approx(60.0)

    def test_rotate_90_swaps_width_height(self):
        """Clockwise 90° rotation should swap width and height of the bounding box."""
        x, y, w, h = texture_rect_to_logical(
            40.0, 30.0, 80.0, 60.0,
            texture_width=400.0,
            texture_height=300.0,
            rotate_steps=1,
        )
        # After 90° CW, the original 80×60 rect becomes a 60×80 bounding box:
        # the input width (80) maps to the output height and input height (60) maps to output width.
        assert w == pytest.approx(60.0)
        assert h == pytest.approx(80.0)

    def test_rotate_180_preserves_size(self):
        """180° rotation preserves width/height of the bounding rect."""
        x, y, w, h = texture_rect_to_logical(
            40.0, 30.0, 80.0, 60.0,
            texture_width=400.0,
            texture_height=300.0,
            rotate_steps=2,
        )
        assert w == pytest.approx(80.0)
        assert h == pytest.approx(60.0)

    def test_invalid_dimensions_return_zeros(self):
        """Invalid input dimensions must all return 0."""
        assert texture_rect_to_logical(
            0.0, 0.0, 0.0, 60.0, texture_width=400.0, texture_height=300.0, rotate_steps=0
        ) == (0.0, 0.0, 0.0, 0.0)
        assert texture_rect_to_logical(
            0.0, 0.0, 80.0, 0.0, texture_width=400.0, texture_height=300.0, rotate_steps=0
        ) == (0.0, 0.0, 0.0, 0.0)
        assert texture_rect_to_logical(
            0.0, 0.0, 80.0, 60.0, texture_width=0.0, texture_height=300.0, rotate_steps=0
        ) == (0.0, 0.0, 0.0, 0.0)

    def test_flip_horizontal_mirrors_x(self):
        """Horizontal flip should mirror the rect's x origin."""
        x_no_flip, _, w_no_flip, _ = texture_rect_to_logical(
            40.0, 30.0, 80.0, 60.0,
            texture_width=400.0,
            texture_height=300.0,
            rotate_steps=0,
        )
        x_flip, _, w_flip, _ = texture_rect_to_logical(
            40.0, 30.0, 80.0, 60.0,
            texture_width=400.0,
            texture_height=300.0,
            rotate_steps=0,
            flip_horizontal=True,
        )
        # Width is unchanged; x origin is mirrored
        assert w_flip == pytest.approx(w_no_flip)
        assert x_flip == pytest.approx(400.0 - x_no_flip - w_no_flip)

