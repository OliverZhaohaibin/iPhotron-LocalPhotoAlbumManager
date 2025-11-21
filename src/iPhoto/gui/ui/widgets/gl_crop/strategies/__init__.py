"""
Interaction strategies for crop mode.

This package implements the Strategy pattern for different crop interactions
(pan vs resize), allowing clean separation of logic.
"""

from .abstract import InteractionStrategy
from .pan_strategy import PanStrategy
from .resize_strategy import ResizeStrategy

__all__ = [
    "InteractionStrategy",
    "PanStrategy",
    "ResizeStrategy",
]
