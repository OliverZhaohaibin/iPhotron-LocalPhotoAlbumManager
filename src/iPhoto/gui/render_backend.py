"""Platform QRhi backend selection for media preview widgets."""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtWidgets import QRhiWidget

_LOGGER = logging.getLogger(__name__)
_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_VALID_BACKENDS = {"auto", "metal", "opengl"}


def _normalised_backend_override() -> str:
    raw_value = os.environ.get("IPHOTO_RHI_BACKEND", "auto").strip().lower()
    if raw_value in _VALID_BACKENDS:
        return raw_value
    _LOGGER.warning(
        "Ignoring unsupported IPHOTO_RHI_BACKEND=%r; expected auto, metal, or opengl",
        raw_value,
    )
    return "auto"


def _qt_api(name: str):
    return getattr(QRhiWidget.Api, name, None)


def select_qrhi_widget_api() -> "QRhiWidget.Api":
    """Return the QRhiWidget backend used by media preview widgets."""

    override = _normalised_backend_override()
    opengl_api = _qt_api("OpenGL")
    metal_api = _qt_api("Metal")
    if opengl_api is None:
        raise RuntimeError("This Qt build does not expose the QRhi OpenGL backend")

    if override == "opengl":
        return opengl_api

    if override == "metal":
        if metal_api is not None:
            return metal_api
        _LOGGER.warning("IPHOTO_RHI_BACKEND=metal requested but this Qt build has no Metal QRhi backend")
        return opengl_api

    if sys.platform == "darwin" and metal_api is not None:
        return metal_api
    return opengl_api


def selected_rhi_backend_name() -> str:
    return qrhi_api_name(select_qrhi_widget_api())


def qrhi_api_name(api: "QRhiWidget.Api | None") -> str:
    if api is None:
        return "unknown"
    name = getattr(api, "name", "")
    if name:
        return str(name).lower()
    return str(api).split(".")[-1].lower()


def is_opengl_api(api: "QRhiWidget.Api | None") -> bool:
    return qrhi_api_name(api) == "opengl"


def should_configure_global_desktop_opengl() -> bool:
    """Return whether app startup should set global desktop OpenGL defaults."""

    if os.environ.get("IPHOTO_DISABLE_OPENGL", "").strip().lower() in _TRUE_ENV_VALUES:
        return False
    override = _normalised_backend_override()
    return not (sys.platform == "darwin" and override != "opengl")
