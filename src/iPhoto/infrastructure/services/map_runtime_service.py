"""Session-owned map runtime capability adapter."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from maps.main import check_opengl_support, choose_default_map_source
from maps.map_sources import (
    has_usable_osmand_native_widget,
    has_usable_osmand_search_extension,
    prefer_osmand_native_widget,
)
from maps.map_widget.native_osmand_widget import probe_native_widget_runtime

from ...application.ports import MapRuntimeCapabilities, MapRuntimePort

_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_MAPS_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "maps"


def _opengl_explicitly_disabled() -> bool:
    return os.environ.get("IPHOTO_DISABLE_OPENGL", "").strip().lower() in _TRUE_ENV_VALUES


class SessionMapRuntimeService(MapRuntimePort):
    """Compute one capability snapshot shared by the active GUI session."""

    def __init__(self, package_root: Path | None = None) -> None:
        self._package_root = (
            Path(package_root).resolve() if package_root is not None else _MAPS_PACKAGE_ROOT
        )
        self._capabilities = self._detect_capabilities()

    def is_available(self) -> bool:
        return self._capabilities.display_available

    def capabilities(self) -> MapRuntimeCapabilities:
        return self._capabilities

    def package_root(self) -> Path:
        return self._package_root

    def refresh(self) -> MapRuntimeCapabilities:
        self._capabilities = self._detect_capabilities()
        return self._capabilities

    def _detect_capabilities(self) -> MapRuntimeCapabilities:
        opengl_disabled = _opengl_explicitly_disabled()
        has_qt_app = QGuiApplication.instance() is not None
        python_gl_available = (
            False
            if opengl_disabled or not has_qt_app
            else check_opengl_support()
        )

        native_widget_available = False
        if (
            has_qt_app
            and
            not opengl_disabled
            and prefer_osmand_native_widget()
            and has_usable_osmand_native_widget(self._package_root)
        ):
            native_widget_available, _ = probe_native_widget_runtime(self._package_root)

        default_source = choose_default_map_source(
            self._package_root,
            use_opengl=python_gl_available,
            native_widget_runtime_available=native_widget_available,
        )
        osmand_extension_available = default_source.kind == "osmand_obf"
        location_search_available = has_usable_osmand_search_extension(self._package_root)

        if osmand_extension_available and native_widget_available:
            preferred_backend = "osmand_native"
        elif osmand_extension_available:
            preferred_backend = "osmand_python"
        elif default_source.kind == "legacy_pbf":
            preferred_backend = "legacy_python"
        else:
            preferred_backend = "unavailable"

        return MapRuntimeCapabilities(
            display_available=preferred_backend != "unavailable",
            preferred_backend=preferred_backend,
            python_gl_available=python_gl_available,
            native_widget_available=native_widget_available,
            osmand_extension_available=osmand_extension_available,
            location_search_available=location_search_available,
            status_message=self._status_message(
                preferred_backend=preferred_backend,
                python_gl_available=python_gl_available,
                osmand_extension_available=osmand_extension_available,
                location_search_available=location_search_available,
            ),
        )

    @staticmethod
    def _status_message(
        *,
        preferred_backend: str,
        python_gl_available: bool,
        osmand_extension_available: bool,
        location_search_available: bool,
    ) -> str:
        if preferred_backend == "osmand_native":
            return "Native OsmAnd widget available."
        if preferred_backend == "osmand_python":
            if python_gl_available:
                return "OsmAnd renderer available with OpenGL."
            return "OsmAnd renderer available; Python map will use CPU fallback."
        if preferred_backend == "legacy_python":
            if osmand_extension_available:
                return "OsmAnd native runtime unavailable; using legacy Python map."
            if location_search_available:
                return "Map extension unavailable; location search remains available."
            if python_gl_available:
                return "Map extension unavailable; using legacy Python map with OpenGL."
            return "Map extension unavailable; using legacy CPU map."
        return "Map runtime unavailable."


__all__ = ["SessionMapRuntimeService"]
