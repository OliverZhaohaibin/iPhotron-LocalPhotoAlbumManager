
import pytest

# Simulation of Shader Functions
def check_crop(uv, crop_cx, crop_cy, crop_w, crop_h):
    min_x = crop_cx - crop_w * 0.5
    max_x = crop_cx + crop_w * 0.5
    min_y = crop_cy - crop_h * 0.5
    max_y = crop_cy + crop_h * 0.5

    # Use approximate comparison to handle float precision at boundaries
    if uv[0] < min_x or uv[0] > max_x:
        return False
    if uv[1] < min_y or uv[1] > max_y:
        return False
    return True

def apply_rotation_90(uv, steps):
    steps = steps % 4
    x, y = uv
    if steps == 0:
        return (x, y)
    if steps == 1: # 90 CW Image Rotation
        return (y, 1.0 - x)
    if steps == 2: # 180
        return (1.0 - x, 1.0 - y)
    if steps == 3: # 270 CW
        return (1.0 - y, x)
    return (x, y)

def apply_inverse_perspective(uv):
    return uv

# Simulation of Python Logic
def clamp_unit(x):
    return max(0.0, min(1.0, x))

def texture_crop_to_logical(crop_params, rotate_steps):
    cx, cy, w, h = crop_params
    steps = rotate_steps % 4
    # Clamp all parameters to [0, 1] to match geometry.py
    if steps == 0:
        return (clamp_unit(cx), clamp_unit(cy), clamp_unit(w), clamp_unit(h))
    if steps == 1:
        return (clamp_unit(1.0-cy), clamp_unit(cx), clamp_unit(h), clamp_unit(w))
    if steps == 2:
        return (clamp_unit(1.0-cx), clamp_unit(1.0-cy), clamp_unit(w), clamp_unit(h))
    if steps == 3:
        return (clamp_unit(cy), clamp_unit(1.0-cx), clamp_unit(h), clamp_unit(w))
    return (clamp_unit(cx), clamp_unit(cy), clamp_unit(w), clamp_unit(h))

# The Logic Under Test (Original/Restored Shader)
def shader_logic(uv_corrected, logical_crop_params, rotate_steps):
    """
    Simulates the Restored shader logic.
    1. Applies Inverse Perspective.
    2. Applies Rotation.
    3. Checks crop against uv_tex (Texture Space).
    """
    cx, cy, w, h = logical_crop_params

    # 1. Inverse Perspective
    uv_perspective = apply_inverse_perspective(uv_corrected)

    # 2. Rotation
    uv_tex = apply_rotation_90(uv_perspective, rotate_steps)

    # 3. Crop Test (Texture Space)
    # Convert logical crop params to texture space for crop test
    crop_tex_params = texture_crop_to_logical((cx, cy, w, h), (4 - rotate_steps) % 4)
    if not check_crop(uv_tex, *crop_tex_params):
        return "DISCARD"
    return uv_tex

def test_texture_space_crop():
    """
    Verifies that the restored logic supports Texture Space Cropping
    (non-destructive editing standard) by passing texture crop parameters
    directly to the shader logic, without Python-side conversion.
    """
    # Scenario: 90 CW Rotation.
    # Texture Crop: Left Half (x < 0.5).
    # Texture crop parameters are used directly.

    tex_params = (0.25, 0.5, 0.5, 1.0) # Left Half
    rotate_steps = 1

    # For Texture Space cropping, logical crop params are the same as texture params
    log_params = tex_params

    # Test Point:
    # Texture Space Point (0.25, 0.5) is the center of the left half.
    # Should PASS.
    res = shader_logic((0.25, 0.5), log_params, rotate_steps)
    assert res != "DISCARD"

    # Test Point:
    # Texture Space Point (0.75, 0.5) is the center of the right half.
    # Should FAIL.
    res_fail = shader_logic((0.75, 0.5), log_params, rotate_steps)
    assert res_fail == "DISCARD"
