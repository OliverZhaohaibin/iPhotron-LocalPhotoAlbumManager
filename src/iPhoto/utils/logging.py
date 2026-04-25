"""Logging helpers for iPhoto."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from typing import Optional

_LOGGER: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    """Return a module-level logger configured for iPhoto."""

    global _LOGGER
    if _LOGGER is None:
        _LOGGER = logging.getLogger("iPhoto")
        if not _LOGGER.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            handler.setFormatter(formatter)
            _LOGGER.addHandler(handler)
            log_path = _default_log_path()
            if log_path is not None:
                try:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    file_handler = RotatingFileHandler(
                        log_path,
                        maxBytes=2_000_000,
                        backupCount=3,
                        encoding="utf-8",
                    )
                    file_handler.setFormatter(formatter)
                    _LOGGER.addHandler(file_handler)
                except OSError:
                    pass
        _LOGGER.setLevel(logging.INFO)
    return _LOGGER


def _default_log_path() -> Path | None:
    override = os.environ.get("IPHOTO_LOG_DIR")
    if override:
        return Path(override).expanduser() / "iPhoto.log"
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return Path(base).expanduser() / "iPhoto" / "iPhoto.log"


logger = get_logger()
