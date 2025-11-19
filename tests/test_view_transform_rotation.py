"""Tests for ViewTransformController rotation coordinate mapping."""

import os
import sys
from unittest.mock import Mock

import pytest

# Add src to path to import directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Import directly from the module file to avoid importing the entire GUI package
import importlib.util

from PySide6.QtCore import QPointF

spec = importlib.util.spec_from_file_location(
    "view_transform_controller",
    os.path.join(os.path.dirname(__file__), "..", "src", "iPhoto", "gui", "ui", "widgets", "view_transform_controller.py")
)
view_transform_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(view_transform_module)
ViewTransformController = view_transform_module.ViewTransformController


@pytest.fixture
def mock_viewer():
    """Create a mock QOpenGLWidget for testing."""
    viewer = Mock()
    viewer.width.return_value = 800
    viewer.height.return_value = 600
    viewer.devicePixelRatioF.return_value = 1.0
    return viewer


@pytest.fixture
def transform_controller(mock_viewer):
    """Create a ViewTransformController instance for testing."""
    def texture_size_provider():
        return (400, 300)  # Landscape image: 400x300
    
    controller = ViewTransformController(
        mock_viewer,
        texture_size_provider=texture_size_provider,
        on_zoom_changed=lambda x: None,
    )
    # Reset to baseline state
    controller.reset_zoom()
    return controller


class TestRotationCoordinateMapping:
    """Test rotation coordinate transformations in ViewTransformController."""

    def test_no_rotation_identity(self, transform_controller):
        """Test that with no rotation, coordinates map correctly."""
        transform_controller.set_rotation_steps(0)
        
        # Texture center (200, 150) should map to viewport center
        tex_size = (400, 300)
        scale = transform_controller.get_effective_scale()
        viewport_pt = transform_controller.image_to_viewport(
            200.0, 150.0, tex_size, scale, 800.0, 600.0, 1.0
        )
        
        # Center should map to center (approximately, considering scaling)
        assert abs(viewport_pt.x() - 400.0) < 50  # Within tolerance
        assert abs(viewport_pt.y() - 300.0) < 50

    def test_rotation_90_degrees(self, transform_controller):
        """Test coordinate mapping with 90° CCW rotation."""
        transform_controller.set_rotation_steps(1)
        
        tex_size = (400, 300)  # Original texture is 400x300 (landscape)
        scale = transform_controller.get_effective_scale()
        
        # Top-left corner (0, 0) of original image
        # After 90° CCW rotation, this should be at bottom-left of rotated view
        viewport_pt = transform_controller.image_to_viewport(
            0.0, 0.0, tex_size, scale, 800.0, 600.0, 1.0
        )
        
        # The point should be in the lower-left quadrant
        assert viewport_pt.x() < 400.0  # Left half
        assert viewport_pt.y() > 300.0  # Bottom half

    def test_rotation_180_degrees(self, transform_controller):
        """Test coordinate mapping with 180° rotation."""
        transform_controller.set_rotation_steps(2)
        
        tex_size = (400, 300)
        scale = transform_controller.get_effective_scale()
        
        # Top-left corner (0, 0) of original image
        # After 180° rotation, this should be at bottom-right
        viewport_pt = transform_controller.image_to_viewport(
            0.0, 0.0, tex_size, scale, 800.0, 600.0, 1.0
        )
        
        # The point should be in the lower-right quadrant
        assert viewport_pt.x() > 400.0  # Right half
        assert viewport_pt.y() > 300.0  # Bottom half

    def test_rotation_270_degrees(self, transform_controller):
        """Test coordinate mapping with 270° CCW rotation."""
        transform_controller.set_rotation_steps(3)
        
        tex_size = (400, 300)
        scale = transform_controller.get_effective_scale()
        
        # Top-left corner (0, 0) of original image
        # After 270° CCW rotation, this should be at top-right
        viewport_pt = transform_controller.image_to_viewport(
            0.0, 0.0, tex_size, scale, 800.0, 600.0, 1.0
        )
        
        # The point should be in the upper-right quadrant
        assert viewport_pt.x() > 400.0  # Right half
        assert viewport_pt.y() < 300.0  # Top half

    def test_inverse_rotation_90_degrees(self, transform_controller):
        """Test that viewport_to_image is the inverse of image_to_viewport for 90°."""
        transform_controller.set_rotation_steps(1)
        
        tex_size = (400, 300)
        scale = transform_controller.get_effective_scale()
        
        # Test a point in the image
        original_x, original_y = 100.0, 50.0
        
        # Convert to viewport and back
        viewport_pt = transform_controller.image_to_viewport(
            original_x, original_y, tex_size, scale, 800.0, 600.0, 1.0
        )
        image_pt = transform_controller.viewport_to_image(
            viewport_pt, tex_size, scale, 800.0, 600.0, 1.0
        )
        
        # Should get back the original coordinates (within tolerance)
        assert abs(image_pt.x() - original_x) < 1.0
        assert abs(image_pt.y() - original_y) < 1.0

    def test_inverse_rotation_180_degrees(self, transform_controller):
        """Test that viewport_to_image is the inverse of image_to_viewport for 180°."""
        transform_controller.set_rotation_steps(2)
        
        tex_size = (400, 300)
        scale = transform_controller.get_effective_scale()
        
        original_x, original_y = 350.0, 250.0
        
        viewport_pt = transform_controller.image_to_viewport(
            original_x, original_y, tex_size, scale, 800.0, 600.0, 1.0
        )
        image_pt = transform_controller.viewport_to_image(
            viewport_pt, tex_size, scale, 800.0, 600.0, 1.0
        )
        
        assert abs(image_pt.x() - original_x) < 1.0
        assert abs(image_pt.y() - original_y) < 1.0

    def test_inverse_rotation_270_degrees(self, transform_controller):
        """Test that viewport_to_image is the inverse of image_to_viewport for 270°."""
        transform_controller.set_rotation_steps(3)
        
        tex_size = (400, 300)
        scale = transform_controller.get_effective_scale()
        
        original_x, original_y = 200.0, 150.0
        
        viewport_pt = transform_controller.image_to_viewport(
            original_x, original_y, tex_size, scale, 800.0, 600.0, 1.0
        )
        image_pt = transform_controller.viewport_to_image(
            viewport_pt, tex_size, scale, 800.0, 600.0, 1.0
        )
        
        assert abs(image_pt.x() - original_x) < 1.0
        assert abs(image_pt.y() - original_y) < 1.0

    def test_rotation_steps_normalization(self, transform_controller):
        """Test that rotation steps are normalized to 0-3 range."""
        transform_controller.set_rotation_steps(5)  # Should become 1 (5 % 4)
        assert transform_controller._rotate_steps == 1
        
        transform_controller.set_rotation_steps(-1)  # Should become 3 ((-1) % 4)
        assert transform_controller._rotate_steps == 3

    def test_convenience_method_respects_rotation(self, transform_controller):
        """Test that convenience methods use rotation state."""
        transform_controller.set_rotation_steps(1)
        
        # Test the convenience convert_image_to_viewport method
        viewport_pt = transform_controller.convert_image_to_viewport(100.0, 100.0)
        
        # Just verify it returns a valid point (detailed testing done above)
        assert isinstance(viewport_pt, QPointF)
        assert viewport_pt.x() != 0.0 or viewport_pt.y() != 0.0

    def test_center_remains_center_all_rotations(self, transform_controller):
        """Test that the image center maps to viewport center for all rotations."""
        tex_size = (400, 300)
        scale = transform_controller.get_effective_scale()
        center_x, center_y = 200.0, 150.0
        
        for steps in range(4):
            transform_controller.set_rotation_steps(steps)
            viewport_pt = transform_controller.image_to_viewport(
                center_x, center_y, tex_size, scale, 800.0, 600.0, 1.0
            )
            
            # Center should remain at viewport center regardless of rotation
            assert abs(viewport_pt.x() - 400.0) < 5.0
            assert abs(viewport_pt.y() - 300.0) < 5.0
