"""Restore intent payload shared across edit/detail playback flows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MediaRestoreRequest:
    """Describe how detail playback should be restored for an asset."""

    path: Path
    reason: str
    duration_sec: float | None = None
