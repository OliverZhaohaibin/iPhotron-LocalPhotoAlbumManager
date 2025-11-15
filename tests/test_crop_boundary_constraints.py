"""Tests for crop boundary constraints to prevent black borders.

This test module validates that the CropBoxState.clamp() method properly
constrains crop rectangles to remain within valid [0, 1] bounds, even when
loading saved crop data that may have floating-point precision errors.
"""

import pytest

# Skip all tests if Qt is not available (headless environment)
try:
    from iPhoto.gui.ui.widgets.gl_crop_utils import CropBoxState
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False
    CropBoxState = None  # type: ignore

pytestmark = pytest.mark.skipif(not QT_AVAILABLE, reason="Qt widgets not available")


def test_clamp_keeps_crop_within_bounds():
    """Test that clamp() ensures crop rectangle stays within [0, 1] bounds."""
    state = CropBoxState()
    
    # Set values that would extend beyond bounds
    state.cx = 0.95
    state.cy = 0.95
    state.width = 0.2
    state.height = 0.2
    
    state.clamp()
    
    # Verify the crop is constrained
    left = state.cx - state.width * 0.5
    right = state.cx + state.width * 0.5
    top = state.cy - state.height * 0.5
    bottom = state.cy + state.height * 0.5
    
    assert left >= 0.0, f"Left boundary {left} should be >= 0.0"
    assert right <= 1.0, f"Right boundary {right} should be <= 1.0"
    assert top >= 0.0, f"Top boundary {top} should be >= 0.0"
    assert bottom <= 1.0, f"Bottom boundary {bottom} should be <= 1.0"


def test_clamp_handles_floating_point_precision_errors():
    """Test that clamp() handles floating-point precision edge cases."""
    state = CropBoxState()
    
    # Simulate floating-point precision error near boundary
    state.cx = 0.5
    state.cy = 0.5
    state.width = 1.0 + 1e-15  # Slightly over 1.0 due to floating-point error
    state.height = 1.0 + 1e-15
    
    state.clamp()
    
    # Should be clamped to exactly 1.0
    assert state.width <= 1.0
    assert state.height <= 1.0
    
    # Bounds should be valid
    left = state.cx - state.width * 0.5
    right = state.cx + state.width * 0.5
    top = state.cy - state.height * 0.5
    bottom = state.cy + state.height * 0.5
    
    assert left >= 0.0
    assert right <= 1.0
    assert top >= 0.0
    assert bottom <= 1.0


def test_clamp_handles_extreme_values():
    """Test that clamp() handles extreme out-of-bound values."""
    state = CropBoxState()
    
    # Set extreme values
    state.cx = 1.5
    state.cy = -0.5
    state.width = 0.5
    state.height = 0.5
    
    state.clamp()
    
    # Should be brought back to valid range
    left = state.cx - state.width * 0.5
    right = state.cx + state.width * 0.5
    top = state.cy - state.height * 0.5
    bottom = state.cy + state.height * 0.5
    
    assert left >= 0.0
    assert right <= 1.0
    assert top >= 0.0
    assert bottom <= 1.0


def test_set_from_mapping_applies_constraints():
    """Test that loading saved crop data applies boundary constraints."""
    state = CropBoxState()
    
    # Simulate saved crop data that might have precision issues
    saved_data = {
        "Crop_CX": 0.9,
        "Crop_CY": 0.9,
        "Crop_W": 0.3,
        "Crop_H": 0.3,
    }
    
    state.set_from_mapping(saved_data)
    
    # Verify constraints were applied
    left = state.cx - state.width * 0.5
    right = state.cx + state.width * 0.5
    top = state.cy - state.height * 0.5
    bottom = state.cy + state.height * 0.5
    
    assert left >= 0.0, "Left boundary should be within bounds"
    assert right <= 1.0, "Right boundary should be within bounds"
    assert top >= 0.0, "Top boundary should be within bounds"
    assert bottom <= 1.0, "Bottom boundary should be within bounds"


def test_clamp_preserves_minimum_dimensions():
    """Test that clamp() respects minimum width and height constraints."""
    state = CropBoxState()
    state.min_width = 0.1
    state.min_height = 0.1
    
    # Try to set dimensions below minimum
    state.cx = 0.5
    state.cy = 0.5
    state.width = 0.01  # Below min_width
    state.height = 0.01  # Below min_height
    
    state.clamp()
    
    # Should be clamped to minimum
    assert state.width >= state.min_width
    assert state.height >= state.min_height


def test_clamp_adjusts_center_when_dimensions_too_large():
    """Test that clamp() adjusts center position when crop is too large for its position."""
    state = CropBoxState()
    
    # Set large dimensions near the edge
    state.cx = 0.9
    state.cy = 0.1
    state.width = 0.5
    state.height = 0.5
    
    state.clamp()
    
    # Center should be adjusted to keep bounds valid
    left = state.cx - state.width * 0.5
    right = state.cx + state.width * 0.5
    top = state.cy - state.height * 0.5
    bottom = state.cy + state.height * 0.5
    
    assert left >= 0.0
    assert right <= 1.0
    assert top >= 0.0
    assert bottom <= 1.0
    # Center should have moved from original position
    assert state.cx != 0.9 or state.cy != 0.1


def test_set_from_mapping_with_edge_case_values():
    """Test loading crop data with edge case values that could cause black borders."""
    state = CropBoxState()
    
    # Values that are technically valid but close to boundaries
    saved_data = {
        "Crop_CX": 0.99999,
        "Crop_CY": 0.00001,
        "Crop_W": 0.5,
        "Crop_H": 0.5,
    }
    
    state.set_from_mapping(saved_data)
    
    # Verify no black borders would appear
    left = state.cx - state.width * 0.5
    right = state.cx + state.width * 0.5
    top = state.cy - state.height * 0.5
    bottom = state.cy + state.height * 0.5
    
    # Use small epsilon for floating-point comparison
    epsilon = 1e-9
    assert left >= -epsilon, f"Left boundary {left} extends beyond valid range"
    assert right <= 1.0 + epsilon, f"Right boundary {right} extends beyond valid range"
    assert top >= -epsilon, f"Top boundary {top} extends beyond valid range"
    assert bottom <= 1.0 + epsilon, f"Bottom boundary {bottom} extends beyond valid range"
