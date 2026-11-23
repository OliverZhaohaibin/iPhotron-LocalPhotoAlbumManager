"""Process-scoped map component installation boundary (no Qt or library state)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from .extension_install import ExtensionInstallError as MapExtensionError
from .extension_install import ExtensionProgress as MapExtensionProgress

__all__ = [
    "MapExtensionError",
    "MapExtensionPort",
    "MapExtensionProgress",
    "MapExtensionRequest",
    "MapExtensionResult",
]


@dataclass(frozen=True)
class MapExtensionRequest:
    package_root: Path
    platform: str
    local_archive_path: Path | None = None
    operation: Literal["install", "prepare"] = "install"
    defer_activation: bool = False


@dataclass(frozen=True)
class MapExtensionResult:
    pending_root: Path
    extension_root: Path
    status: Literal["installed", "pending_restart", "missing"] = "installed"


class MapExtensionPort(Protocol):
    def execute(
        self, request: MapExtensionRequest, progress: MapExtensionProgress
    ) -> MapExtensionResult: ...

    def download_url(self, platform: str) -> str | None: ...

    def supports_local_install(self, platform: str) -> bool:
        """Whether the catalogue accepts a manual archive for this platform."""
        ...
