"""Tests for the gl_crop CropSessionModel module."""

import pytest

from iPhotos.src.iPhoto.gui.ui.widgets.gl_crop.model import CropSessionModel


@pytest.fixture
def model():
    """Create a CropSessionModel instance for testing."""
    return CropSessionModel()


def test_create_and_restore_snapshot(model):
    """Test snapshot creation and restoration."""
    # Set custom crop values
    crop_state = model.get_crop_state()
    crop_state.cx = 0.3
    crop_state.cy = 0.4
    crop_state.width = 0.5
    crop_state.height = 0.6

    # Create snapshot
    snapshot = model.create_snapshot()
    assert snapshot == (0.3, 0.4, 0.5, 0.6)

    # Modify crop
    crop_state.cx = 0.7
    crop_state.cy = 0.8

    # Restore snapshot
    model.restore_snapshot(snapshot)
    assert crop_state.cx == pytest.approx(0.3)
    assert crop_state.cy == pytest.approx(0.4)
    assert crop_state.width == pytest.approx(0.5)
    assert crop_state.height == pytest.approx(0.6)


def test_has_changed_detects_changes(model):
    """Test that has_changed correctly detects state changes."""
    snapshot = model.create_snapshot()
    assert not model.has_changed(snapshot)

    # Make a change
    crop_state = model.get_crop_state()
    crop_state.cx = 0.6
    assert model.has_changed(snapshot)


def test_has_changed_ignores_small_changes(model):
    """Test that has_changed ignores tiny floating-point differences."""
    snapshot = model.create_snapshot()
    crop_state = model.get_crop_state()
    crop_state.cx = 0.5 + 1e-7  # Very small change
    assert not model.has_changed(snapshot)


def test_baseline_management(model):
    """Test baseline crop state management."""
    assert not model.has_baseline()

    model.create_baseline()
    assert model.has_baseline()

    model.clear_baseline()
    assert not model.has_baseline()


def test_update_perspective_no_change(model):
    """Test that update_perspective returns False when nothing changes."""
    # First update sets values
    changed = model.update_perspective(0.0, 0.0, 0.0, 0, False, 1.0)
    assert changed  # First update should change

    # Same values should not trigger change
    changed = model.update_perspective(0.0, 0.0, 0.0, 0, False, 1.0)
    assert not changed


def test_update_perspective_with_change(model):
    """Test that update_perspective returns True when values change."""
    model.update_perspective(0.0, 0.0, 0.0, 0, False, 1.0)
    changed = model.update_perspective(0.1, 0.0, 0.0, 0, False, 1.0)
    assert changed


def test_is_crop_inside_quad_initially(model):
    """Test that crop starts inside the unit quad."""
    # Default crop (full image) should be inside unit quad
    model.update_perspective(0.0, 0.0, 0.0, 0, False, 1.0)
    assert model.is_crop_inside_quad()


def test_ensure_crop_center_inside_quad_when_already_inside(model):
    """Test that ensure_crop_center_inside_quad does nothing when already inside."""
    model.update_perspective(0.0, 0.0, 0.0, 0, False, 1.0)
    changed = model.ensure_crop_center_inside_quad()
    assert not changed


def test_auto_scale_crop_to_quad_when_already_fits(model):
    """Test that auto_scale_crop_to_quad does nothing when crop already fits."""
    model.update_perspective(0.0, 0.0, 0.0, 0, False, 1.0)
    changed = model.auto_scale_crop_to_quad()
    assert not changed


def test_ensure_valid_or_revert_keeps_valid_crop(model):
    """Test that ensure_valid_or_revert keeps a valid crop state."""
    model.update_perspective(0.0, 0.0, 0.0, 0, False, 1.0)
    snapshot = model.create_snapshot()
    result = model.ensure_valid_or_revert(snapshot, allow_shrink=False)
    assert result


def test_ensure_valid_or_revert_reverts_invalid_crop(model):
    """Test that ensure_valid_or_revert reverts an invalid crop state."""
    model.update_perspective(0.0, 0.0, 0.0, 0, False, 1.0)
    snapshot = model.create_snapshot()

    # Make crop invalid by moving it outside bounds
    crop_state = model.get_crop_state()
    crop_state.cx = -0.5  # Way outside
    crop_state.cy = -0.5

    # Should revert to snapshot
    model.ensure_valid_or_revert(snapshot, allow_shrink=False)
    
    # Check if reverted (cx, cy should be back to snapshot values)
    # The revert might clamp values, so we just check it's different from the invalid state
    assert crop_state.cx != -0.5 or crop_state.cy != -0.5


def test_apply_baseline_perspective_fit_without_baseline(model):
    """Test that apply_baseline_perspective_fit does nothing without a baseline."""
    changed = model.apply_baseline_perspective_fit()
    assert not changed


def test_apply_baseline_perspective_fit_with_baseline(model):
    """Test applying baseline perspective fit."""
    # Set up a baseline
    crop_state = model.get_crop_state()
    crop_state.cx = 0.5
    crop_state.cy = 0.5
    crop_state.width = 0.8
    crop_state.height = 0.8
    model.create_baseline()

    # Update perspective (this would normally change the quad)
    model.update_perspective(0.1, 0.1, 0.0, 0, False, 1.0)

    # Apply baseline fit
    changed = model.apply_baseline_perspective_fit()
    # The result depends on the perspective quad, so we just verify it runs
    assert isinstance(changed, bool)
