
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

perspective_math_path = os.path.join(
    project_root, 'src', 'iPhoto', 'gui', 'ui', 'widgets', 'perspective_math.py'
)
perspective_math_path = os.path.abspath(perspective_math_path)

gl_renderer_path = os.path.join(
    project_root, 'src', 'iPhoto', 'gui', 'ui', 'widgets', 'gl_renderer.py'
)
gl_renderer_path = os.path.abspath(gl_renderer_path)

# Load perspective_math first as it is imported by gl_renderer
load_module_from_file('iPhoto.gui.ui.widgets.perspective_math', perspective_math_path)

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
    with patch('iPhoto.gui.ui.widgets.gl_renderer.QOpenGLShaderProgram') as MockProgram, \
         patch('iPhoto.gui.ui.widgets.gl_renderer.QOpenGLVertexArrayObject') as MockVAO, \
         patch('iPhoto.gui.ui.widgets.gl_renderer.gl') as MockGL, \
         patch('iPhoto.gui.ui.widgets.gl_renderer._load_shader_source', return_value="void main() {}"):

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
