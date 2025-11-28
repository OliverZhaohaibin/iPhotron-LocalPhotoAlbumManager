"""
Test to verify the shader coordinate refactoring logic.

This test verifies that the transformation logic used in the shader
(inverse rotation to convert texture->logical and forward rotation to
convert logical->texture) produces correct results.
"""

import sys
from pathlib import Path

import pytest

# Import geometry module for transformation logic
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


def apply_rotation_90_python(uv: tuple[float, float], rotate_steps: int) -> tuple[float, float]:
    """Python implementation of apply_rotation_90 from shader."""
    x, y = uv
    steps = rotate_steps % 4
    if steps == 1:
        # 90° CW: (x,y) -> (y, 1-x)
        return (y, 1.0 - x)
    elif steps == 2:
        # 180°: (x,y) -> (1-x, 1-y)
        return (1.0 - x, 1.0 - y)
    elif steps == 3:
        # 270° CW: (x,y) -> (1-y, x)
        return (1.0 - y, x)
    return uv


def apply_inverse_rotation_90_python(uv: tuple[float, float], rotate_steps: int) -> tuple[float, float]:
    """Python implementation of apply_inverse_rotation_90 from shader."""
    x, y = uv
    steps = rotate_steps % 4
    if steps == 1:
        # Inverse of 90° CW is 270° CW: (x,y) -> (1-y, x)
        return (1.0 - y, x)
    elif steps == 2:
        # Inverse of 180° is 180°: (x,y) -> (1-x, 1-y)
        return (1.0 - x, 1.0 - y)
    elif steps == 3:
        # Inverse of 270° CW is 90° CW: (x,y) -> (y, 1-x)
        return (y, 1.0 - x)
    return uv


class TestShaderRotationInverse:
    """Test that shader rotation and inverse rotation are true inverses."""
    
    def test_inverse_rotation_roundtrip(self):
        """Applying rotation then inverse should return original coordinates."""
        test_points = [
            (0.0, 0.0),
            (1.0, 1.0),
            (0.5, 0.5),
            (0.3, 0.7),
            (0.2, 0.8),
        ]
        
        for point in test_points:
            for rotate_steps in range(4):
                # Apply forward rotation (texture -> rotated texture)
                rotated = apply_rotation_90_python(point, rotate_steps)
                # Apply inverse rotation (rotated texture -> original)
                recovered = apply_inverse_rotation_90_python(rotated, rotate_steps)
                
                assert recovered[0] == pytest.approx(point[0], abs=1e-6), \
                    f"X mismatch at rotation {rotate_steps}: {point} -> {rotated} -> {recovered}"
                assert recovered[1] == pytest.approx(point[1], abs=1e-6), \
                    f"Y mismatch at rotation {rotate_steps}: {point} -> {rotated} -> {recovered}"
    
    def test_inverse_matches_geometry_module(self):
        """Verify shader inverse rotation matches geometry.py texture_crop_to_logical."""
        # Test that the shader's inverse rotation produces the same result as
        # the geometry module's texture_crop_to_logical transformation
        
        test_coords = [
            (0.3, 0.7),
            (0.5, 0.5),
            (0.2, 0.8),
        ]
        
        for coord in test_coords:
            for rotate_steps in range(4):
                # What geometry.py produces (texture -> logical)
                cx, cy, w, h = geometry.texture_crop_to_logical(
                    (coord[0], coord[1], 1.0, 1.0), rotate_steps
                )
                geometry_result = (cx, cy)
                
                # What the shader produces (texture -> logical via inverse rotation)
                shader_result = apply_inverse_rotation_90_python(coord, rotate_steps)
                
                assert shader_result[0] == pytest.approx(geometry_result[0], abs=1e-6), \
                    f"X mismatch at rotation {rotate_steps}: geometry={geometry_result}, shader={shader_result}"
                assert shader_result[1] == pytest.approx(geometry_result[1], abs=1e-6), \
                    f"Y mismatch at rotation {rotate_steps}: geometry={geometry_result}, shader={shader_result}"


class TestLogicalCoordinateFlow:
    """Test the complete flow: Python passes logical coords, shader converts to texture."""
    
    def test_python_to_shader_flow(self):
        """
        Test the complete coordinate flow:
        1. Python has texture coordinates from disk
        2. Python converts to logical coordinates (via geometry.texture_crop_to_logical)
        3. Python passes logical coords to shader
        4. Shader converts logical back to texture (via apply_rotation_90)
        5. Result should match original texture coordinates
        """
        
        original_texture_coords = (0.3, 0.7)
        
        for rotate_steps in range(4):
            # Step 1-2: Python converts texture -> logical (this is what Python does now)
            logical_cx, logical_cy, _, _ = geometry.texture_crop_to_logical(
                (original_texture_coords[0], original_texture_coords[1], 1.0, 1.0),
                rotate_steps
            )
            logical_coords = (logical_cx, logical_cy)
            
            # Step 3-4: Shader receives logical coords and converts back to texture
            # (this is what the new shader does)
            recovered_texture = apply_rotation_90_python(logical_coords, rotate_steps)
            
            # Step 5: Verify we get back the original texture coordinates
            assert recovered_texture[0] == pytest.approx(original_texture_coords[0], abs=1e-6), \
                f"X mismatch at rotation {rotate_steps}: {original_texture_coords} -> {logical_coords} -> {recovered_texture}"
            assert recovered_texture[1] == pytest.approx(original_texture_coords[1], abs=1e-6), \
                f"Y mismatch at rotation {rotate_steps}: {original_texture_coords} -> {logical_coords} -> {recovered_texture}"


class TestCropBoundaryLogic:
    """Test that crop boundaries work correctly in logical space."""
    
    def test_crop_at_rotation_0(self):
        """At 0° rotation, logical space == texture space."""
        # Crop center at (0.5, 0.5) with size (0.6, 0.4)
        crop_cx, crop_cy = 0.5, 0.5
        rotate_steps = 0
        
        # Convert to logical (should be same at 0°)
        logical_cx, logical_cy, _, _ = geometry.texture_crop_to_logical(
            (crop_cx, crop_cy, 0.6, 0.4), rotate_steps
        )
        
        assert logical_cx == pytest.approx(crop_cx)
        assert logical_cy == pytest.approx(crop_cy)
    
    def test_crop_boundaries_at_90_degrees(self):
        """At 90° rotation, crop boundaries should be correctly transformed."""
        # Texture space: crop at (0.3, 0.7) with size (0.4, 0.6)
        texture_crop = (0.3, 0.7, 0.4, 0.6)
        rotate_steps = 1  # 90° CW
        
        # Convert to logical space (what Python passes to shader)
        logical_cx, logical_cy, logical_w, logical_h = geometry.texture_crop_to_logical(
            texture_crop, rotate_steps
        )
        
        # Verify the transformation matches our expectations
        # At 90° CW: (x', y') = (1-y, x) and dimensions swap
        expected_cx = 1.0 - 0.7  # = 0.3
        expected_cy = 0.3
        expected_w = 0.6  # height becomes width
        expected_h = 0.4  # width becomes height
        
        assert logical_cx == pytest.approx(expected_cx, abs=1e-6)
        assert logical_cy == pytest.approx(expected_cy, abs=1e-6)
        assert logical_w == pytest.approx(expected_w, abs=1e-6)
        assert logical_h == pytest.approx(expected_h, abs=1e-6)
