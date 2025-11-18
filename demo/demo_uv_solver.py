"""
Standalone demonstration of UV-space constraint solver behavior.

This script demonstrates the key mathematical concepts without requiring
the full iPhoto package to be imported.
"""

import numpy as np
import math


def build_perspective_matrix(vertical: float, horizontal: float) -> np.ndarray:
    """Build perspective transformation matrix."""
    clamped_v = max(-1.0, min(1.0, float(vertical)))
    clamped_h = max(-1.0, min(1.0, float(horizontal)))
    if abs(clamped_v) <= 1e-5 and abs(clamped_h) <= 1e-5:
        return np.identity(3, dtype=np.float32)

    angle_scale = math.radians(20.0)
    angle_x = clamped_v * angle_scale
    angle_y = clamped_h * angle_scale

    cos_x = math.cos(angle_x)
    sin_x = math.sin(angle_x)
    cos_y = math.cos(angle_y)
    sin_y = math.sin(angle_y)

    rx = np.array([[1.0, 0.0, 0.0], [0.0, cos_x, -sin_x], [0.0, sin_x, cos_x]], dtype=np.float32)
    ry = np.array([[cos_y, 0.0, sin_y], [0.0, 1.0, 0.0], [-sin_y, 0.0, cos_y]], dtype=np.float32)

    return np.matmul(ry, rx).astype(np.float32)


def inverse_project_point(screen_point: tuple[float, float], matrix: np.ndarray) -> tuple[float, float]:
    """Map screen-space point to texture UV coordinates."""
    x, y = screen_point
    centered = np.array([(x * 2.0) - 1.0, (y * 2.0) - 1.0, 1.0], dtype=np.float32)
    warped = matrix @ centered
    denom = float(warped[2])
    if abs(denom) < 1e-6:
        denom = 1e-6 if denom >= 0.0 else -1e-6
    nx = float(warped[0]) / denom
    ny = float(warped[1]) / denom
    return ((nx + 1.0) * 0.5, (ny + 1.0) * 0.5)


def calculate_safety_padding(tex_w: int, tex_h: int, pixels: int = 3) -> tuple[float, float]:
    """Calculate safety padding in UV space."""
    if tex_w <= 0 or tex_h <= 0:
        return (0.0, 0.0)
    return (float(pixels) / float(tex_w), float(pixels) / float(tex_h))


def validate_rect(
    left: float, top: float, right: float, bottom: float,
    matrix: np.ndarray, tex_w: int, tex_h: int, padding: int = 3
) -> bool:
    """Check if rectangle corners are within safe UV bounds."""
    epsilon_u, epsilon_v = calculate_safety_padding(tex_w, tex_h, padding)
    corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
    
    for cx, cy in corners:
        u, v = inverse_project_point((cx, cy), matrix)
        if u < epsilon_u or u > (1.0 - epsilon_u):
            return False
        if v < epsilon_v or v > (1.0 - epsilon_v):
            return False
    return True


def find_maximum_safe_scale_binary(
    cx: float, cy: float, width: float, height: float,
    matrix: np.ndarray, tex_w: int, tex_h: int, padding: int = 3
) -> float:
    """Find maximum safe scale using binary search."""
    # Quick check if full scale works
    if validate_rect(cx - width * 0.5, cy - height * 0.5,
                     cx + width * 0.5, cy + height * 0.5,
                     matrix, tex_w, tex_h, padding):
        return 1.0
    
    # Binary search for maximum safe scale
    min_scale = 0.0
    max_scale = 1.0
    
    for _ in range(10):  # 10 iterations for pixel-level precision
        mid_scale = (min_scale + max_scale) * 0.5
        test_width = width * mid_scale
        test_height = height * mid_scale
        
        if validate_rect(cx - test_width * 0.5, cy - test_height * 0.5,
                        cx + test_width * 0.5, cy + test_height * 0.5,
                        matrix, tex_w, tex_h, padding):
            min_scale = mid_scale
        else:
            max_scale = mid_scale
        
        if abs(max_scale - min_scale) < 0.001:
            break
    
    return min_scale


def constrain_rect(
    left: float, top: float, right: float, bottom: float,
    matrix: np.ndarray, tex_w: int, tex_h: int, padding: int = 3
) -> tuple[float, float, float, float]:
    """Use binary search to find maximum safe scale for rectangle."""
    cx = (left + right) * 0.5
    cy = (top + bottom) * 0.5
    width = right - left
    height = bottom - top
    
    # Use binary search to find maximum safe scale
    safe_scale = find_maximum_safe_scale_binary(cx, cy, width, height, matrix, tex_w, tex_h, padding)
    
    # Apply the safe scale
    final_width = width * safe_scale
    final_height = height * safe_scale
    
    return (cx - final_width * 0.5, cy - final_height * 0.5,
            cx + final_width * 0.5, cy + final_height * 0.5)


def main():
    """Run demonstration."""
    print("╔" + "═" * 78 + "╗")
    print("║" + " UV-SPACE BINARY SEARCH CONSTRAINT SOLVER ".center(78) + "║")
    print("╚" + "═" * 78 + "╝")
    
    texture_size = (2000, 2000)
    tex_w, tex_h = texture_size
    
    scenarios = [
        ("No Perspective", 0.0, 0.0, (0.0, 0.0, 1.0, 1.0)),
        ("Moderate Perspective", 0.5, 0.3, (0.1, 0.1, 0.9, 0.9)),
        ("Strong Perspective", 0.8, 0.6, (0.1, 0.1, 0.9, 0.9)),
        ("Extreme: Max Vertical", 1.0, 0.0, (0.2, 0.2, 0.8, 0.8)),
        ("Extreme: Max Horizontal", 0.0, 1.0, (0.2, 0.2, 0.8, 0.8)),
        ("Extreme: Both Directions", 1.0, -1.0, (0.25, 0.25, 0.75, 0.75)),
    ]
    
    all_valid = True
    
    for name, v, h, (left, top, right, bottom) in scenarios:
        print(f"\n{'=' * 80}")
        print(f"Scenario: {name}")
        print(f"{'=' * 80}")
        print(f"Perspective: vertical={v:+.2f}, horizontal={h:+.2f}")
        
        # Build matrix
        matrix = build_perspective_matrix(v, h)
        
        # Check original
        orig_width = right - left
        orig_height = bottom - top
        print(f"Original rect: ({left:.3f}, {top:.3f}) to ({right:.3f}, {bottom:.3f})")
        print(f"Original size: {orig_width:.3f} x {orig_height:.3f}")
        
        is_valid_orig = validate_rect(left, top, right, bottom, matrix, tex_w, tex_h, 3)
        print(f"Valid before: {is_valid_orig}")
        
        # Apply constraint
        new_left, new_top, new_right, new_bottom = constrain_rect(
            left, top, right, bottom, matrix, tex_w, tex_h, 3
        )
        
        new_width = new_right - new_left
        new_height = new_bottom - new_top
        print(f"Constrained rect: ({new_left:.3f}, {new_top:.3f}) to ({new_right:.3f}, {new_bottom:.3f})")
        print(f"Constrained size: {new_width:.3f} x {new_height:.3f}")
        
        is_valid_new = validate_rect(new_left, new_top, new_right, new_bottom, matrix, tex_w, tex_h, 3)
        print(f"Valid after: {is_valid_new} {'✅' if is_valid_new else '❌'}")
        
        if not is_valid_new:
            all_valid = False
        
        shrink_pct = (1.0 - new_width / orig_width) * 100
        print(f"Shrinkage: {shrink_pct:.1f}%")
        
        # Show sample UV coordinates
        epsilon_u, epsilon_v = calculate_safety_padding(tex_w, tex_h, 3)
        print(f"Safety bounds: [{epsilon_u:.6f}, {1.0-epsilon_u:.6f}] x [{epsilon_v:.6f}, {1.0-epsilon_v:.6f}]")
        
        # Check a corner
        u, v = inverse_project_point((new_left, new_top), matrix)
        in_bounds_u = epsilon_u <= u <= (1.0 - epsilon_u)
        in_bounds_v = epsilon_v <= v <= (1.0 - epsilon_v)
        print(f"Top-left corner UV: ({u:.6f}, {v:.6f}) - in bounds: {in_bounds_u and in_bounds_v}")
    
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    
    if all_valid:
        print("✅ All scenarios passed!")
        print("\nKey achievements:")
        print("  • All constrained rectangles have valid UV coordinates")
        print("  • Safety padding based on texture resolution (3 pixels)")
        print("  • Works correctly at extreme perspective angles (±1.0)")
        print("  • Eliminates black edges by guaranteeing UV ∈ [ε, 1-ε]")
        return 0
    else:
        print("❌ Some scenarios failed")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
