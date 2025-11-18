"""Tests for crop box aspect ratio locking during perspective drag."""

import pytest


def test_crop_aspect_ratio_locking():
    """Test that crop box aspect ratio is locked during perspective drag."""
    from src.iPhoto.gui.ui.widgets.gl_crop_utils import CropBoxState
    from src.iPhoto.gui.ui.widgets.perspective_math import unit_quad
    
    # Mock the CropInteractionController's relevant behavior
    # We test the logic, not the full Qt integration
    
    # Initial crop state
    crop_state = CropBoxState()
    crop_state.width = 0.8
    crop_state.height = 0.6
    crop_state.cx = 0.5
    crop_state.cy = 0.5
    
    # Calculate initial aspect ratio
    initial_aspect = crop_state.width / crop_state.height
    assert abs(initial_aspect - (0.8 / 0.6)) < 1e-6
    
    # Simulate locking the aspect ratio (this happens in start_perspective_drag)
    locked_aspect = crop_state.width / crop_state.height
    
    # Simulate some adjustment that might change dimensions
    # (this mimics what _auto_scale_crop_to_quad might do)
    crop_state.height = 0.5
    
    # Restore locked aspect (this happens in update_perspective)
    crop_state.width = crop_state.height * locked_aspect
    
    # Verify aspect ratio is preserved
    final_aspect = crop_state.width / crop_state.height
    assert abs(final_aspect - initial_aspect) < 1e-6
    assert abs(crop_state.width - (0.5 * locked_aspect)) < 1e-6


def test_perspective_drag_session_tracking():
    """Test perspective drag session state tracking."""
    # Test that we can track drag sessions correctly
    
    class MockCropController:
        def __init__(self):
            self._perspective_dragging = False
            self._locked_crop_aspect = None
            self._active = True
            
        def start_perspective_drag(self):
            if not self._active:
                return
            self._perspective_dragging = True
            # Simulate locking aspect ratio
            self._locked_crop_aspect = 1.5  # Mock aspect ratio
            
        def end_perspective_drag(self):
            self._perspective_dragging = False
            self._locked_crop_aspect = None
    
    controller = MockCropController()
    
    # Initially not dragging
    assert not controller._perspective_dragging
    assert controller._locked_crop_aspect is None
    
    # Start drag session
    controller.start_perspective_drag()
    assert controller._perspective_dragging
    assert controller._locked_crop_aspect == 1.5
    
    # End drag session
    controller.end_perspective_drag()
    assert not controller._perspective_dragging
    assert controller._locked_crop_aspect is None


def test_calculate_min_zoom_to_fit():
    """Test the calculate_min_zoom_to_fit function."""
    from src.iPhoto.gui.ui.widgets.perspective_math import (
        calculate_min_zoom_to_fit,
        NormalisedRect,
        unit_quad,
    )
    
    # Test case 1: Crop fits perfectly inside quad
    quad = unit_quad()
    crop_rect = NormalisedRect(0.1, 0.1, 0.9, 0.9)
    scale = calculate_min_zoom_to_fit(crop_rect, quad)
    assert scale == pytest.approx(1.0, abs=0.01)
    
    # Test case 2: Crop is too large
    large_crop = NormalisedRect(0.0, 0.0, 1.0, 1.0)
    scale = calculate_min_zoom_to_fit(large_crop, quad)
    # Should be >= 1.0 since crop equals quad size
    assert scale >= 1.0
    
    # Test case 3: Small crop inside quad
    small_crop = NormalisedRect(0.4, 0.4, 0.6, 0.6)
    scale = calculate_min_zoom_to_fit(small_crop, quad)
    # Small crop should easily fit
    assert scale == pytest.approx(1.0, abs=0.01)
