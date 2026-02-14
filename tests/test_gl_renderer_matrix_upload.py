
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch
import importlib.util

# Setup dummy package structure to avoid triggering real __init__.py imports
# that require system dependencies (libpulse, etc) we might not have or want to load.
# This essentially isolates the module under test.

def setup_dummy_packages():
    packages = [
        'iPhoto',
        'iPhoto.core',
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
    if spec is None:
        raise ImportError(f"Could not load spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

setup_dummy_packages()

this_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(this_dir)
widgets_dir = os.path.join(
    project_root, 'src', 'iPhoto', 'gui', 'ui', 'widgets',
)
core_dir = os.path.join(
    project_root, 'src', 'iPhoto', 'core',
)

selective_color_path = os.path.abspath(os.path.join(core_dir, 'selective_color_resolver.py'))
perspective_math_path = os.path.abspath(os.path.join(widgets_dir, 'perspective_math.py'))
gl_shader_manager_path = os.path.abspath(os.path.join(widgets_dir, 'gl_shader_manager.py'))
gl_texture_manager_path = os.path.abspath(os.path.join(widgets_dir, 'gl_texture_manager.py'))
gl_uniform_state_path = os.path.abspath(os.path.join(widgets_dir, 'gl_uniform_state.py'))
gl_offscreen_path = os.path.abspath(os.path.join(widgets_dir, 'gl_offscreen.py'))
gl_renderer_path = os.path.abspath(os.path.join(widgets_dir, 'gl_renderer.py'))

# Load helper modules before gl_renderer (which imports them via relative imports)
load_module_from_file('iPhoto.core.selective_color_resolver', selective_color_path)
load_module_from_file('iPhoto.gui.ui.widgets.perspective_math', perspective_math_path)
load_module_from_file('iPhoto.gui.ui.widgets.gl_shader_manager', gl_shader_manager_path)
load_module_from_file('iPhoto.gui.ui.widgets.gl_texture_manager', gl_texture_manager_path)
load_module_from_file('iPhoto.gui.ui.widgets.gl_uniform_state', gl_uniform_state_path)
load_module_from_file('iPhoto.gui.ui.widgets.gl_offscreen', gl_offscreen_path)

# Load gl_renderer
gl_renderer_mod = load_module_from_file('iPhoto.gui.ui.widgets.gl_renderer', gl_renderer_path)

GLRenderer = gl_renderer_mod.GLRenderer
from PySide6.QtCore import QPointF

@pytest.fixture
def mock_gl_funcs():
    return MagicMock()

@pytest.fixture
def renderer(mock_gl_funcs):
    # Patch QOpenGLShaderProgram to avoid needing a real GL context
    with patch('iPhoto.gui.ui.widgets.gl_shader_manager.QOpenGLShaderProgram') as MockProgram, \
         patch('iPhoto.gui.ui.widgets.gl_shader_manager.QOpenGLVertexArrayObject') as MockVAO, \
         patch('iPhoto.gui.ui.widgets.gl_shader_manager.gl') as MockGL, \
         patch('iPhoto.gui.ui.widgets.gl_shader_manager._load_shader_source', return_value="void main() {}"):

        MockProgram.return_value.addShaderFromSourceCode.return_value = True
        MockProgram.return_value.link.return_value = True
        MockProgram.return_value.uniformLocation.return_value = 1

        MockVAO.return_value.isCreated.return_value = True

        # Ensure glGenBuffers returns a value compatible with int()
        MockGL.glGenBuffers.return_value = 1

        renderer = GLRenderer(mock_gl_funcs)
        renderer.initialize_resources()
        return renderer

def test_render_upload_matrix_transpose_flag(renderer, mock_gl_funcs):
    """Verify that glUniformMatrix3fv is called with the correct transpose flag."""

    view_width = 800.0
    view_height = 600.0
    scale = 1.0
    pan = QPointF(0.0, 0.0)
    adjustments = {}

    renderer._texture_id = 1
    renderer._texture_width = 100
    renderer._texture_height = 100

    renderer.render(
        view_width=view_width,
        view_height=view_height,
        scale=scale,
        pan=pan,
        adjustments=adjustments
    )

    calls = mock_gl_funcs.glUniformMatrix3fv.call_args_list

    found = False
    for call in calls:
        args, kwargs = call
        location = args[0]
        transpose = args[2]

        if location == 1:
            found = True
            # Expect transpose=1 (True) to confirm the FIX
            assert transpose == 1, "Expected transpose=1 (True)"

    assert found, "glUniformMatrix3fv was not called for uPerspectiveMatrix"
