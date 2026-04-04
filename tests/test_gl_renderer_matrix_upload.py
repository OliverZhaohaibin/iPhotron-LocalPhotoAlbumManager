
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
from iPhoto.core.selective_color_resolver import NUM_RANGES

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

def test_render_uploads_perspective_rows_as_vec3_uniforms(renderer, mock_gl_funcs, monkeypatch):
    """Perspective transforms should upload through vec3 rows, not matrix uniforms."""

    view_width = 800.0
    view_height = 600.0
    scale = 1.0
    pan = QPointF(0.0, 0.0)
    adjustments = {}
    perspective_matrix = gl_renderer_mod.np.array(
        [
            [1.0, 2.0, 3.0],
            [4.0, 5.0, 6.0],
            [7.0, 8.0, 9.0],
        ],
        dtype=gl_renderer_mod.np.float32,
    )

    renderer._texture_id = 1
    renderer._texture_width = 100
    renderer._texture_height = 100

    renderer._uniform_locations.clear()
    renderer._uniform_locations.update(
        {
            "uPerspectiveRow0": 301,
            "uPerspectiveRow1": 302,
            "uPerspectiveRow2": 303,
        }
    )
    raw_uniform_matrix3fv = MagicMock()
    monkeypatch.setattr(gl_renderer_mod.gl, "glUniformMatrix3fv", raw_uniform_matrix3fv)

    with patch.object(gl_renderer_mod, "build_perspective_matrix", return_value=perspective_matrix):
        renderer.render(
            view_width=view_width,
            view_height=view_height,
            scale=scale,
            pan=pan,
            adjustments=adjustments
        )

    calls_by_location = {
        call.args[0]: call.args[1:]
        for call in mock_gl_funcs.glUniform3f.call_args_list
        if call.args[0] in {301, 302, 303}
    }

    assert calls_by_location[301] == pytest.approx(tuple(perspective_matrix[0]))
    assert calls_by_location[302] == pytest.approx(tuple(perspective_matrix[1]))
    assert calls_by_location[303] == pytest.approx(tuple(perspective_matrix[2]))
    raw_uniform_matrix3fv.assert_not_called()


def test_initialize_resources_queries_selective_color_array_elements(mock_gl_funcs):
    """Selective-color array uniforms should be queried by explicit element name."""

    with patch('iPhoto.gui.ui.widgets.gl_shader_manager.QOpenGLShaderProgram') as MockProgram, \
         patch('iPhoto.gui.ui.widgets.gl_shader_manager.QOpenGLVertexArrayObject') as MockVAO, \
         patch('iPhoto.gui.ui.widgets.gl_shader_manager.gl') as MockGL, \
         patch('iPhoto.gui.ui.widgets.gl_shader_manager._load_shader_source', return_value="void main() {}"):

        MockProgram.return_value.addShaderFromSourceCode.return_value = True
        MockProgram.return_value.link.return_value = True
        MockProgram.return_value.uniformLocation.side_effect = lambda name: 1
        MockVAO.return_value.isCreated.return_value = True
        MockGL.glGenBuffers.return_value = 1

        renderer = GLRenderer(mock_gl_funcs)
        renderer.initialize_resources()

        queried_names = {
            call.args[0] for call in MockProgram.return_value.uniformLocation.call_args_list
        }

        for idx in range(NUM_RANGES):
            assert f"uSCRange0[{idx}]" in queried_names
            assert f"uSCRange1[{idx}]" in queried_names
        assert "uSCRange0" not in queried_names
        assert "uSCRange1" not in queried_names


def test_render_uploads_selective_color_ranges_per_element(renderer, mock_gl_funcs, monkeypatch):
    """Selective-color uniforms should be uploaded per element for Mesa compatibility."""

    renderer._texture_id = 1
    renderer._texture_width = 100
    renderer._texture_height = 100
    renderer._uniform_locations.clear()
    renderer._uniform_locations.update(
        {
            **{f"uSCRange0[{idx}]": 100 + idx for idx in range(NUM_RANGES)},
            **{f"uSCRange1[{idx}]": 200 + idx for idx in range(NUM_RANGES)},
        }
    )
    raw_gl_uniform4fv = MagicMock()
    monkeypatch.setattr(gl_renderer_mod.gl, "glUniform4fv", raw_gl_uniform4fv)

    renderer.render(
        view_width=800.0,
        view_height=600.0,
        scale=1.0,
        pan=QPointF(0.0, 0.0),
        adjustments={
            "SelectiveColor_Ranges": [
                [0.0, 0.5, 0.1, 0.2, 0.3]
                for _ in range(NUM_RANGES)
            ]
        },
    )

    called_locations = {call.args[0] for call in mock_gl_funcs.glUniform4f.call_args_list}

    for idx in range(NUM_RANGES):
        assert 100 + idx in called_locations
        assert 200 + idx in called_locations
    raw_gl_uniform4fv.assert_not_called()
