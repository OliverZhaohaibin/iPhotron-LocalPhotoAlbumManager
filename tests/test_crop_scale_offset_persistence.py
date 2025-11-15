"""Tests for crop scale and offset persistence in .ipo files."""

import tempfile
from pathlib import Path

import pytest

from iPhoto.io.sidecar import load_adjustments, save_adjustments


def test_crop_scale_and_offset_saved_and_loaded():
    """Test that Crop_Scale, Crop_OX, and Crop_OY are saved and loaded correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        asset_path = Path(tmpdir) / "test_image.jpg"
        asset_path.touch()
        
        # Create adjustments with crop scale and offset
        adjustments = {
            "Crop_CX": 0.6,
            "Crop_CY": 0.4,
            "Crop_W": 0.8,
            "Crop_H": 0.7,
            "Crop_Scale": 2.5,
            "Crop_OX": 100.5,
            "Crop_OY": -50.3,
            "Light_Master": 0.0,
            "Light_Enabled": True,
        }
        
        # Save adjustments
        sidecar_path = save_adjustments(asset_path, adjustments)
        assert sidecar_path.exists()
        
        # Load adjustments back
        loaded = load_adjustments(asset_path)
        
        # Verify crop box values are preserved
        assert pytest.approx(loaded["Crop_CX"], rel=1e-5) == 0.6
        assert pytest.approx(loaded["Crop_CY"], rel=1e-5) == 0.4
        assert pytest.approx(loaded["Crop_W"], rel=1e-5) == 0.8
        assert pytest.approx(loaded["Crop_H"], rel=1e-5) == 0.7
        
        # Verify new scale and offset values are preserved
        assert pytest.approx(loaded["Crop_Scale"], rel=1e-5) == 2.5
        assert pytest.approx(loaded["Crop_OX"], rel=1e-5) == 100.5
        assert pytest.approx(loaded["Crop_OY"], rel=1e-5) == -50.3


def test_crop_scale_and_offset_defaults():
    """Test that Crop_Scale, Crop_OX, and Crop_OY default to correct values when not present."""
    with tempfile.TemporaryDirectory() as tmpdir:
        asset_path = Path(tmpdir) / "test_image.jpg"
        asset_path.touch()
        
        # Create adjustments without scale and offset
        adjustments = {
            "Crop_CX": 0.5,
            "Crop_CY": 0.5,
            "Crop_W": 1.0,
            "Crop_H": 1.0,
        }
        
        # Save adjustments
        save_adjustments(asset_path, adjustments)
        
        # Load adjustments back
        loaded = load_adjustments(asset_path)
        
        # Verify defaults are applied
        assert pytest.approx(loaded["Crop_Scale"], rel=1e-5) == 1.0
        assert pytest.approx(loaded["Crop_OX"], rel=1e-5) == 0.0
        assert pytest.approx(loaded["Crop_OY"], rel=1e-5) == 0.0


def test_crop_scale_and_offset_with_extreme_values():
    """Test that extreme values for scale and offset are handled correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        asset_path = Path(tmpdir) / "test_image.jpg"
        asset_path.touch()
        
        # Create adjustments with extreme values
        adjustments = {
            "Crop_CX": 0.5,
            "Crop_CY": 0.5,
            "Crop_W": 1.0,
            "Crop_H": 1.0,
            "Crop_Scale": 40.0,  # Maximum scale
            "Crop_OX": 999999.0,  # Large offset
            "Crop_OY": -999999.0,  # Large negative offset
        }
        
        # Save adjustments
        save_adjustments(asset_path, adjustments)
        
        # Load adjustments back
        loaded = load_adjustments(asset_path)
        
        # Verify extreme values are preserved
        assert pytest.approx(loaded["Crop_Scale"], rel=1e-5) == 40.0
        assert pytest.approx(loaded["Crop_OX"], rel=1e-5) == 999999.0
        assert pytest.approx(loaded["Crop_OY"], rel=1e-5) == -999999.0


def test_edit_session_includes_crop_scale_and_offset():
    """Test that EditSession includes Crop_Scale, Crop_OX, and Crop_OY fields."""
    try:
        from iPhoto.gui.ui.models.edit_session import EditSession
    except ImportError as e:
        pytest.skip(f"GUI imports not available: {e}")
    
    session = EditSession()
    
    # Verify fields exist with correct defaults
    assert session.value("Crop_Scale") == 1.0
    assert session.value("Crop_OX") == 0.0
    assert session.value("Crop_OY") == 0.0
    
    # Test setting values
    session.set_value("Crop_Scale", 2.5)
    session.set_value("Crop_OX", 100.0)
    session.set_value("Crop_OY", -50.0)
    
    assert session.value("Crop_Scale") == 2.5
    assert session.value("Crop_OX") == 100.0
    assert session.value("Crop_OY") == -50.0
    
    # Test range clamping for scale
    session.set_value("Crop_Scale", 50.0)  # Above max
    assert session.value("Crop_Scale") == 40.0  # Should be clamped to max
    
    session.set_value("Crop_Scale", 0.01)  # Below min
    assert session.value("Crop_Scale") == 0.02  # Should be clamped to min


def test_edit_session_reset_includes_crop_scale_and_offset():
    """Test that EditSession.reset() resets Crop_Scale, Crop_OX, and Crop_OY to defaults."""
    try:
        from iPhoto.gui.ui.models.edit_session import EditSession
    except ImportError as e:
        pytest.skip(f"GUI imports not available: {e}")
    
    session = EditSession()
    
    # Set non-default values
    session.set_value("Crop_Scale", 3.0)
    session.set_value("Crop_OX", 200.0)
    session.set_value("Crop_OY", -100.0)
    
    # Reset session
    session.reset()
    
    # Verify defaults are restored
    assert session.value("Crop_Scale") == 1.0
    assert session.value("Crop_OX") == 0.0
    assert session.value("Crop_OY") == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
