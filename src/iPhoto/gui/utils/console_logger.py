from __future__ import annotations

import logging
import sys

_INSTALLED_HANDLERS: set[str] = set()


def ensure_console_logger(
    logger: logging.Logger,
    handler_name: str,
    *,
    level: int = logging.INFO,
) -> None:
    if handler_name in _INSTALLED_HANDLERS:
        return
    for handler in logger.handlers:
        if getattr(handler, "name", None) == handler_name:
            _INSTALLED_HANDLERS.add(handler_name)
            return
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.name = handler_name
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    _INSTALLED_HANDLERS.add(handler_name)
