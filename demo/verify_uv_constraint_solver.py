#!/usr/bin/env python3
"""
Verification script for UV-space constraint solver.

This script demonstrates that the new UV-space validation approach correctly
constrains crop boxes at various perspective angles, including extreme cases.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
from iPhoto.gui.ui.widgets.perspective_math import (
    NormalisedRect,
    build_perspective_matrix,
    calculate_texture_safety_padding,
    constrain_rect_to_uv_bounds,
    validate_crop_corners_in_uv_space,
)


def test_scenario(
    name: str,
    vertical: float,
    horizontal: float,
    rect: NormalisedRect,
    texture_size: tuple[int, int],
):
    """Test a specific perspective scenario and print results."""
    print(f"\n{'=' * 70}")
    print(f"Scenario: {name}")
    print(f"{'=' * 70}")
    print(f"Perspective: vertical={vertical:+.2f}, horizontal={horizontal:+.2f}")
    print(f"Texture size: {texture_size[0]}x{texture_size[1]} pixels")
    print(f"Original rect: {rect.left:.3f}, {rect.top:.3f}, {rect.right:.3f}, {rect.bottom:.3f}")
    print(f"Original size: {rect.width:.3f} x {rect.height:.3f}")
    
    # Build perspective matrix
    matrix = build_perspective_matrix(vertical, horizontal)
    
    # Calculate safety padding
    epsilon_u, epsilon_v = calculate_texture_safety_padding(
        texture_size[0], texture_size[1], padding_pixels=3
    )
    print(f"Safety padding: εu={epsilon_u:.6f}, εv={epsilon_v:.6f}")
    
    # Validate original rectangle
    is_valid_orig, uv_corners_orig = validate_crop_corners_in_uv_space(
        rect, matrix, texture_size, padding_pixels=3
    )
    print(f"\nOriginal rect valid: {is_valid_orig}")
    if not is_valid_orig:
        print("UV corners (out of bounds):")
        for i, (u, v) in enumerate(uv_corners_orig):
            out_u = u < epsilon_u or u > (1.0 - epsilon_u)
            out_v = v < epsilon_v or v > (1.0 - epsilon_v)
            marker = " ⚠️" if (out_u or out_v) else ""
            print(f"  Corner {i}: u={u:.6f}, v={v:.6f}{marker}")
    
    # Apply constraint solver
    constrained_rect = constrain_rect_to_uv_bounds(
        rect, matrix, texture_size, padding_pixels=3, max_iterations=10
    )
    
    # Validate constrained rectangle
    is_valid_constrained, uv_corners_constrained = validate_crop_corners_in_uv_space(
        constrained_rect, matrix, texture_size, padding_pixels=3
    )
    
    print(f"\nConstrained rect: {constrained_rect.left:.3f}, {constrained_rect.top:.3f}, "
          f"{constrained_rect.right:.3f}, {constrained_rect.bottom:.3f}")
    print(f"Constrained size: {constrained_rect.width:.3f} x {constrained_rect.height:.3f}")
    print(f"Constrained rect valid: {is_valid_constrained} ✅")
    
    if is_valid_constrained:
        print("UV corners (all within bounds):")
        for i, (u, v) in enumerate(uv_corners_constrained):
            print(f"  Corner {i}: u={u:.6f}, v={v:.6f}")
    
    # Calculate shrink percentage
    shrink_pct = (1.0 - constrained_rect.width / rect.width) * 100
    print(f"\nShrink amount: {shrink_pct:.1f}%")
    
    return is_valid_constrained


def main():
    """Run verification tests."""
    print("╔" + "═" * 68 + "╗")
    print("║" + " UV-SPACE CONSTRAINT SOLVER VERIFICATION ".center(68) + "║")
    print("╚" + "═" * 68 + "╝")
    
    texture_size = (2000, 2000)  # High resolution texture
    
    scenarios = [
        (
            "No Perspective (Identity)",
            0.0, 0.0,
            NormalisedRect(0.0, 0.0, 1.0, 1.0),
        ),
        (
            "Moderate Perspective",
            0.3, 0.2,
            NormalisedRect(0.1, 0.1, 0.9, 0.9),
        ),
        (
            "Strong Perspective",
            0.7, 0.5,
            NormalisedRect(0.05, 0.05, 0.95, 0.95),
        ),
        (
            "Extreme Perspective (Max Vertical)",
            1.0, 0.0,
            NormalisedRect(0.1, 0.1, 0.9, 0.9),
        ),
        (
            "Extreme Perspective (Max Horizontal)",
            0.0, 1.0,
            NormalisedRect(0.1, 0.1, 0.9, 0.9),
        ),
        (
            "Extreme Perspective (Both Directions)",
            1.0, -1.0,
            NormalisedRect(0.2, 0.2, 0.8, 0.8),
        ),
        (
            "Edge Case: Full Frame at Moderate Angle",
            0.5, 0.5,
            NormalisedRect(0.0, 0.0, 1.0, 1.0),
        ),
    ]
    
    results = []
    for name, v, h, rect in scenarios:
        is_valid = test_scenario(name, v, h, rect, texture_size)
        results.append((name, is_valid))
    
    # Summary
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    
    all_passed = all(valid for _, valid in results)
    
    for name, valid in results:
        status = "✅ PASS" if valid else "❌ FAIL"
        print(f"{status}: {name}")
    
    print(f"\n{'=' * 70}")
    if all_passed:
        print("✅ All scenarios passed! UV-space constraint solver is working correctly.")
        return 0
    else:
        print("❌ Some scenarios failed. Please review the implementation.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
