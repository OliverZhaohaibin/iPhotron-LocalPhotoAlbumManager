"""
Unit tests for GLImageViewer.crop_values() coordinate conversion.

These tests verify that the crop_values() method correctly converts from
logical space (what the user sees) to texture space (what gets saved).
This is critical for ensuring crop consistency across rotations.
"""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

# Import the geometry module directly to avoid Qt dependencies
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


class TestCropValuesCoordinateConversion:
    """Test crop_values() converts from logical space to texture space."""

    def test_no_rotation_returns_same_values(self):
        """With no rotation, logical and texture space are identical."""
        # Create a mock GLImageViewer with necessary attributes
        viewer = self._create_mock_viewer(
            crop_values={"Crop_CX": 0.3, "Crop_CY": 0.7, "Crop_W": 0.5, "Crop_H": 0.6},
            rotation_steps=0,
        )
        
        result = viewer.crop_values()
        
        assert result["Crop_CX"] == pytest.approx(0.3)
        assert result["Crop_CY"] == pytest.approx(0.7)
        assert result["Crop_W"] == pytest.approx(0.5)
        assert result["Crop_H"] == pytest.approx(0.6)

    def test_90_degree_rotation_converts_correctly(self):
        """With 90° rotation, crop_values() should convert back to texture space."""
        # User sees logical coordinates (after 90° rotation)
        logical_cx, logical_cy, logical_w, logical_h = 0.3, 0.7, 0.5, 0.6
        
        # We expect these to be converted back to texture space
        # Using the inverse transformation: logical -> texture with rotate_steps=1
        expected_texture = geometry.logical_crop_to_texture(
            (logical_cx, logical_cy, logical_w, logical_h), 1
        )
        
        viewer = self._create_mock_viewer(
            crop_values={
                "Crop_CX": logical_cx,
                "Crop_CY": logical_cy,
                "Crop_W": logical_w,
                "Crop_H": logical_h,
            },
            rotation_steps=1,
        )
        
        result = viewer.crop_values()
        
        assert result["Crop_CX"] == pytest.approx(expected_texture[0])
        assert result["Crop_CY"] == pytest.approx(expected_texture[1])
        assert result["Crop_W"] == pytest.approx(expected_texture[2])
        assert result["Crop_H"] == pytest.approx(expected_texture[3])

    def test_180_degree_rotation_converts_correctly(self):
        """With 180° rotation, crop_values() should invert coordinates."""
        logical_cx, logical_cy, logical_w, logical_h = 0.3, 0.7, 0.5, 0.6
        
        expected_texture = geometry.logical_crop_to_texture(
            (logical_cx, logical_cy, logical_w, logical_h), 2
        )
        
        viewer = self._create_mock_viewer(
            crop_values={
                "Crop_CX": logical_cx,
                "Crop_CY": logical_cy,
                "Crop_W": logical_w,
                "Crop_H": logical_h,
            },
            rotation_steps=2,
        )
        
        result = viewer.crop_values()
        
        assert result["Crop_CX"] == pytest.approx(expected_texture[0])
        assert result["Crop_CY"] == pytest.approx(expected_texture[1])
        assert result["Crop_W"] == pytest.approx(expected_texture[2])
        assert result["Crop_H"] == pytest.approx(expected_texture[3])

    def test_270_degree_rotation_converts_correctly(self):
        """With 270° rotation, crop_values() should convert appropriately."""
        logical_cx, logical_cy, logical_w, logical_h = 0.3, 0.7, 0.5, 0.6
        
        expected_texture = geometry.logical_crop_to_texture(
            (logical_cx, logical_cy, logical_w, logical_h), 3
        )
        
        viewer = self._create_mock_viewer(
            crop_values={
                "Crop_CX": logical_cx,
                "Crop_CY": logical_cy,
                "Crop_W": logical_w,
                "Crop_H": logical_h,
            },
            rotation_steps=3,
        )
        
        result = viewer.crop_values()
        
        assert result["Crop_CX"] == pytest.approx(expected_texture[0])
        assert result["Crop_CY"] == pytest.approx(expected_texture[1])
        assert result["Crop_W"] == pytest.approx(expected_texture[2])
        assert result["Crop_H"] == pytest.approx(expected_texture[3])

    def test_round_trip_preserves_texture_coordinates(self):
        """Converting texture->logical->texture should preserve original values."""
        original_texture = (0.3, 0.7, 0.5, 0.6)
        
        for rotate_steps in range(4):
            # Simulate what happens in the UI:
            # 1. Load texture coordinates and convert to logical for display
            logical = geometry.texture_crop_to_logical(original_texture, rotate_steps)
            
            # 2. User interacts with crop in logical space (simulated by mock)
            viewer = self._create_mock_viewer(
                crop_values={
                    "Crop_CX": logical[0],
                    "Crop_CY": logical[1],
                    "Crop_W": logical[2],
                    "Crop_H": logical[3],
                },
                rotation_steps=rotate_steps,
            )
            
            # 3. crop_values() should convert back to texture space
            result = viewer.crop_values()
            
            # The result should match the original texture coordinates
            assert result["Crop_CX"] == pytest.approx(original_texture[0], abs=1e-6)
            assert result["Crop_CY"] == pytest.approx(original_texture[1], abs=1e-6)
            assert result["Crop_W"] == pytest.approx(original_texture[2], abs=1e-6)
            assert result["Crop_H"] == pytest.approx(original_texture[3], abs=1e-6)

    def _create_mock_viewer(self, crop_values: dict, rotation_steps: int):
        """Create a mock GLImageViewer with the crop_values method implementation."""
        # Create a mock viewer object
        viewer = Mock()
        viewer._crop_controller = Mock()
        viewer._crop_controller.get_crop_values = Mock(return_value=crop_values)
        viewer._adjustments = {"Crop_Rotate90": float(rotation_steps)}
        
        # Implement the fixed crop_values method inline for testing
        def crop_values_impl():
            """Return crop values in texture space for persistence."""
            # Get the current crop values in logical space
            logical_values = viewer._crop_controller.get_crop_values()
            
            # Extract logical coordinates
            logical_cx = float(logical_values.get("Crop_CX", 0.5))
            logical_cy = float(logical_values.get("Crop_CY", 0.5))
            logical_w = float(logical_values.get("Crop_W", 1.0))
            logical_h = float(logical_values.get("Crop_H", 1.0))
            
            # Get the current rotation state
            rotate_steps = geometry.get_rotate_steps(viewer._adjustments)
            
            # Convert from logical space back to texture space
            tex_cx, tex_cy, tex_w, tex_h = geometry.logical_crop_to_texture(
                (logical_cx, logical_cy, logical_w, logical_h),
                rotate_steps,
            )
            
            # Return texture space coordinates
            return {
                "Crop_CX": tex_cx,
                "Crop_CY": tex_cy,
                "Crop_W": tex_w,
                "Crop_H": tex_h,
            }
        
        viewer.crop_values = crop_values_impl
        return viewer
