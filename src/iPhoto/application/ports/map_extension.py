"""Process-scoped map component installation boundary (no Qt or library state)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

MapExtensionProgress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class MapExtensionRequest:
    package_root: Path
    platform: str
    local_archive_path: Path | None = None
    network_mode: Literal["system", "direct"] = "system"
    operation: Literal["install", "prepare"] = "install"
    defer_activation: bool = False


@dataclass(frozen=True)
class MapExtensionResult:
    pending_root: Path
    extension_root: Path
    status: Literal["installed", "pending_restart", "missing"] = "installed"


class MapExtensionError(Exception):
    """Stable error category plus diagnostics safe to display or copy."""

    def __init__(self, category: str, stage: str, *, code: int | None = None, detail: str = ""):
        self.category = category
        self.stage = stage
        self.code = code
        self.detail = detail
        super().__init__(f"stage={stage}; category={category}; code={code}; {detail}")


class MapExtensionPort(Protocol):
    def execute(
        self, request: MapExtensionRequest, progress: MapExtensionProgress
    ) -> MapExtensionResult: ...

    def download_url(self, platform: str) -> str | None: ...
