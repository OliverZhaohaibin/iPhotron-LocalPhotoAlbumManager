"""Helpers for lightweight detail-view performance diagnostics."""

from __future__ import annotations

import logging
import os

LOGGER = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def detail_profile_enabled() -> bool:
    """Return whether detail-view profiling logs should be emitted."""

    return os.environ.get("IPHOTO_DETAIL_PROFILE", "").strip().lower() in _TRUTHY


def log_detail_profile(
    component: str,
    stage: str,
    elapsed_ms: float | None = None,
    **details: object,
) -> None:
    """Emit a structured profiling line when detail profiling is enabled."""

    if not detail_profile_enabled():
        return

    suffix = ""
    if details:
        suffix = " " + " ".join(f"{key}={value}" for key, value in details.items())

    if elapsed_ms is None:
        LOGGER.info("[detail_profile][%s] %s%s", component, stage, suffix)
        return

    LOGGER.info(
        "[detail_profile][%s] %s %.1fms%s",
        component,
        stage,
        elapsed_ms,
        suffix,
    )


__all__ = ["detail_profile_enabled", "log_detail_profile"]
