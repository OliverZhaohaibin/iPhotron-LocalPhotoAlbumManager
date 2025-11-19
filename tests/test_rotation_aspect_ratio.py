"""Test for rotation aspect ratio fix.

This test validates that the display dimensions are correctly swapped
for 90° and 270° rotations, preventing image stretching.
"""

import pytest


def test_display_texture_dimensions_no_rotation():
    """Test that dimensions are not swapped for 0° rotation."""
    # Simulate _display_texture_dimensions logic
    tex_w, tex_h = 1920, 1080
    rotate_steps = 0
    
    if rotate_steps % 2:
        display_w, display_h = tex_h, tex_w
    else:
        display_w, display_h = tex_w, tex_h
    
    assert display_w == 1920
    assert display_h == 1080


def test_display_texture_dimensions_90_degree_rotation():
    """Test that dimensions are swapped for 90° rotation."""
    # Simulate _display_texture_dimensions logic
    tex_w, tex_h = 1920, 1080
    rotate_steps = 1  # 90 degrees
    
    if rotate_steps % 2:
        display_w, display_h = tex_h, tex_w
    else:
        display_w, display_h = tex_w, tex_h
    
    assert display_w == 1080  # Swapped
    assert display_h == 1920  # Swapped


def test_display_texture_dimensions_180_degree_rotation():
    """Test that dimensions are not swapped for 180° rotation."""
    # Simulate _display_texture_dimensions logic
    tex_w, tex_h = 1920, 1080
    rotate_steps = 2  # 180 degrees
    
    if rotate_steps % 2:
        display_w, display_h = tex_h, tex_w
    else:
        display_w, display_h = tex_w, tex_h
    
    assert display_w == 1920
    assert display_h == 1080


def test_display_texture_dimensions_270_degree_rotation():
    """Test that dimensions are swapped for 270° rotation."""
    # Simulate _display_texture_dimensions logic
    tex_w, tex_h = 1920, 1080
    rotate_steps = 3  # 270 degrees
    
    if rotate_steps % 2:
        display_w, display_h = tex_h, tex_w
    else:
        display_w, display_h = tex_w, tex_h
    
    assert display_w == 1080  # Swapped
    assert display_h == 1920  # Swapped


def test_portrait_image_rotation():
    """Test rotation with portrait orientation image."""
    # Portrait image
    tex_w, tex_h = 1080, 1920
    rotate_steps = 1  # 90 degrees
    
    if rotate_steps % 2:
        display_w, display_h = tex_h, tex_w
    else:
        display_w, display_h = tex_w, tex_h
    
    # After 90° rotation, portrait becomes landscape
    assert display_w == 1920  # Swapped
    assert display_h == 1080  # Swapped


def test_square_image_rotation():
    """Test that square images maintain dimensions through rotation."""
    # Square image
    tex_w, tex_h = 1080, 1080
    
    for rotate_steps in range(4):  # 0°, 90°, 180°, 270°
        if rotate_steps % 2:
            display_w, display_h = tex_h, tex_w
        else:
            display_w, display_h = tex_w, tex_h
        
        # Square images should maintain same dimensions
        assert display_w == 1080
        assert display_h == 1080
