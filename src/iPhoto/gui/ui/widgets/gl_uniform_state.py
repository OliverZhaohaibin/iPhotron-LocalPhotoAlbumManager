# -*- coding: utf-8 -*-
"""Uniform setter helpers for the GL renderer."""

from __future__ import annotations

import numpy as np


class UniformState:
    """Wraps uniform-setting GL calls for the active shader program."""

    def __init__(self, gl_funcs, uniform_locations: dict[str, int]) -> None:
        self._gl_funcs = gl_funcs
        self._uniform_locations = uniform_locations

    def _set_uniform1i(self, name: str, value: int) -> None:
        location = self._uniform_locations.get(name, -1)
        if location != -1:
            self._gl_funcs.glUniform1i(location, int(value))

    def _set_uniform1f(self, name: str, value: float) -> None:
        location = self._uniform_locations.get(name, -1)
        if location != -1:
            self._gl_funcs.glUniform1f(location, float(value))

    def _set_uniform2f(self, name: str, x: float, y: float) -> None:
        location = self._uniform_locations.get(name, -1)
        if location != -1:
            self._gl_funcs.glUniform2f(location, float(x), float(y))

    def _set_uniform3f(self, name: str, x: float, y: float, z: float) -> None:
        location = self._uniform_locations.get(name, -1)
        if location != -1:
            self._gl_funcs.glUniform3f(location, float(x), float(y), float(z))

    def _set_uniform4f(self, name: str, x: float, y: float, z: float, w: float) -> None:
        location = self._uniform_locations.get(name, -1)
        if location != -1:
            self._gl_funcs.glUniform4f(location, float(x), float(y), float(z), float(w))

    def _set_uniform_matrix3(self, name: str, matrix: np.ndarray) -> None:
        """Set a 3x3 uniform matrix in the currently bound shader program."""

        location = self._uniform_locations.get(name, -1)
        if location == -1:
            return

        # Keep this path compatible with both desktop GL and GLES contexts.
        # GLES requires transpose=GL_FALSE for glUniformMatrix* calls, so we
        # pre-transpose the row-major numpy matrix before upload.
        matrix_data = tuple(float(v) for v in np.ascontiguousarray(matrix.T, dtype=np.float32).ravel())

        self._gl_funcs.glUniformMatrix3fv(
            location,
            1,
            0,  # GL_FALSE for GLES compatibility
            matrix_data,
        )
