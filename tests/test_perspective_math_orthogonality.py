
import sys
import os
import types
import pytest
import numpy as np
import importlib.util

# Setup dummy package structure
def setup_dummy_packages():
    packages = [
        'iPhoto',
        'iPhoto.gui',
        'iPhoto.gui.ui',
        'iPhoto.gui.ui.widgets',
    ]
    for pkg in packages:
        if pkg not in sys.modules:
            m = types.ModuleType(pkg)
            m.__path__ = []
            sys.modules[pkg] = m

def load_module_from_file(module_name, file_path):
    if module_name in sys.modules and hasattr(sys.modules[module_name], '__file__') and sys.modules[module_name].__file__ == file_path:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

this_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(this_dir)

perspective_math_path = os.path.join(
    project_root, 'src', 'iPhoto', 'gui', 'ui', 'widgets', 'perspective_math.py'
)
perspective_math_path = os.path.abspath(perspective_math_path)
pm = load_module_from_file('iPhoto.gui.ui.widgets.perspective_math', perspective_math_path)
build_perspective_matrix = pm.build_perspective_matrix

def test_straighten_orthogonality():
    """Verify that the perspective matrix creates a rigid rotation in physical space."""
    straighten_angle = 45.0
    aspect_ratio = 2.0

    # Calculate matrix
    matrix = build_perspective_matrix(
        vertical=0.0,
        horizontal=0.0,
        image_aspect_ratio=aspect_ratio,
        straighten_degrees=straighten_angle,
        rotate_steps=0,
        flip_horizontal=False
    )

    # Vectors in Screen UV space
    v_x = np.array([1.0, 0.0, 0.0])
    v_y = np.array([0.0, 1.0, 0.0])

    # Map to Texture UV space
    t_x = matrix @ v_x
    t_y = matrix @ v_y

    # Map to Physical Texture space
    p_x = np.array([t_x[0] * aspect_ratio, t_x[1]])
    p_y = np.array([t_y[0] * aspect_ratio, t_y[1]])

    # Check orthogonality (Dot product should be 0)
    dot_prod = np.dot(p_x, p_y)

    assert abs(dot_prod) < 1e-5, f"Matrix produces non-orthogonal transformation. Dot product: {dot_prod}"

    # Also check lengths.
    # p_x length should be aspect_ratio (since x axis [0,1] maps to [0, aspect])
    # p_y length should be 1.0
    len_x = np.linalg.norm(p_x)
    len_y = np.linalg.norm(p_y)

    assert abs(len_x - aspect_ratio) < 1e-5, f"X axis scaling incorrect. Expected {aspect_ratio}, got {len_x}"
    assert abs(len_y - 1.0) < 1e-5, f"Y axis scaling incorrect. Expected 1.0, got {len_y}"

def test_straighten_orthogonality_inverse():
    """Verify that the inverse behavior (which happens if aspect ratio is inverted) is what breaks it."""
    # This acts as a negative test to confirm our test logic detects the problem if matrix was built wrong.
    # If we pass aspect_ratio=0.5 but treat physical as 2.0.

    aspect_ratio_matrix = 0.5
    aspect_ratio_phys = 2.0
    straighten_angle = 45.0

    matrix = build_perspective_matrix(
        vertical=0.0,
        horizontal=0.0,
        image_aspect_ratio=aspect_ratio_matrix,
        straighten_degrees=straighten_angle,
    )

    v_x = np.array([1.0, 0.0, 0.0])
    v_y = np.array([0.0, 1.0, 0.0])
    t_x = matrix @ v_x
    t_y = matrix @ v_y

    p_x = np.array([t_x[0] * aspect_ratio_phys, t_x[1]])
    p_y = np.array([t_y[0] * aspect_ratio_phys, t_y[1]])

    dot_prod = np.dot(p_x, p_y)

    # This SHOULD fail orthogonality
    assert abs(dot_prod) > 0.1, "Mismatching aspect ratios should produce non-orthogonal transform"
