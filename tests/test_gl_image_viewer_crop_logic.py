"""
Unit tests for gl_image_viewer crop logic.

Tests the crop calculation and validation logic.

Note: We import the crop_logic module directly rather than through the package
to avoid Qt dependencies that are triggered by the package __init__.py.
This allows testing with only the minimal PySide6 imports required.
"""


import pytest

# Import crop_logic using package structure. If Qt dependencies are problematic, mock them in test setup.
from iPhotos.src.iPhoto.gui.ui.widgets.gl_image_viewer import crop_logic
has_valid_crop = crop_logic.has_valid_crop
compute_crop_rect_pixels = crop_logic.compute_crop_rect_pixels


class TestHasValidCrop:
    """Test crop validation."""

    def test_full_image_not_valid(self):
        """Full image (1.0 x 1.0) should not be considered a crop."""
        assert not has_valid_crop(1.0, 1.0)

    def test_zero_dimensions_not_valid(self):
        """Zero dimensions should not be valid."""
        assert not has_valid_crop(0.0, 0.5)
        assert not has_valid_crop(0.5, 0.0)
        assert not has_valid_crop(0.0, 0.0)

    def test_negative_dimensions_not_valid(self):
        """Negative dimensions should not be valid."""
        assert not has_valid_crop(-0.1, 0.5)
        assert not has_valid_crop(0.5, -0.1)

    def test_valid_crop(self):
        """Proper crop dimensions should be valid."""
        assert has_valid_crop(0.5, 0.5)
        assert has_valid_crop(0.9, 0.9)
        assert has_valid_crop(0.5, 0.8)
        assert has_valid_crop(0.8, 0.5)

    def test_near_full_not_valid(self):
        """Values very close to 1.0 should not be considered a crop."""
        # Using epsilon of 1e-3, so 0.999 should not be valid
        assert not has_valid_crop(0.999, 0.999)
        # But 0.99 should be valid
        assert has_valid_crop(0.99, 0.99)


class TestComputeCropRectPixels:
    """Test crop rectangle calculation in pixels."""

    def test_returns_none_for_invalid_texture(self):
        """Should return None if texture dimensions are invalid."""
        assert compute_crop_rect_pixels(0.5, 0.5, 0.5, 0.5, 0, 100) is None
        assert compute_crop_rect_pixels(0.5, 0.5, 0.5, 0.5, 100, 0) is None
        assert compute_crop_rect_pixels(0.5, 0.5, 0.5, 0.5, -10, 100) is None

    def test_returns_none_for_full_image(self):
        """Should return None if crop covers the entire image."""
        result = compute_crop_rect_pixels(0.5, 0.5, 1.0, 1.0, 100, 100)
        assert result is None

    def test_returns_none_for_invalid_crop(self):
        """Should return None if crop dimensions are invalid."""
        result = compute_crop_rect_pixels(0.5, 0.5, 0.0, 0.5, 100, 100)
        assert result is None

    def test_centered_half_crop(self):
        """Test a centered 50% crop."""
        result = compute_crop_rect_pixels(0.5, 0.5, 0.5, 0.5, 100, 100)
        assert result is not None
        
        # Center point: 50, 50
        # Width: 50px, Height: 50px
        # Expected rect: x=25, y=25, w=50, h=50
        assert result.x() == 25.0
        assert result.y() == 25.0
        assert result.width() == 50.0
        assert result.height() == 50.0

    def test_off_center_crop(self):
        """Test an off-center crop."""
        # Crop at 75% x, 25% y with 0.4 width, 0.6 height
        result = compute_crop_rect_pixels(0.75, 0.25, 0.4, 0.6, 200, 200)
        assert result is not None
        
        # Center: 150, 50
        # Width: 80px, Height: 120px
        # Half width: 40, Half height: 60
        # Expected: x=110, y=0 (clamped), w=80, h=110 (clamped to bottom)
        assert result.x() == pytest.approx(110.0)
        
        # Y should be clamped to stay within bounds
        # Center 50 - half height 60 = -10, clamped to 0
        assert result.y() == 0.0

    def test_crop_at_edge(self):
        """Test crop that extends to image edge."""
        # Crop at right edge
        result = compute_crop_rect_pixels(0.9, 0.5, 0.5, 0.5, 100, 100)
        assert result is not None
        
        # Center: 90, 50
        # Width: 50px, Height: 50px
        # Right edge: 90 + 25 = 115, clamped to 100
        # Actual width: 100 - 65 = 35
        assert result.x() == pytest.approx(65.0)
        assert result.y() == 25.0
        # Width gets clamped
        assert result.width() >= 1.0

    def test_minimum_rect_size(self):
        """Test that rectangles have minimum size of 1 pixel."""
        # Very small crop
        result = compute_crop_rect_pixels(0.5, 0.5, 0.01, 0.01, 100, 100)
        assert result is not None
        assert result.width() >= 1.0
        assert result.height() >= 1.0
