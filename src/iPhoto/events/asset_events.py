"""Asset-level events shared by application services and GUI projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .domain_events import DomainEvent


@dataclass(frozen=True)
class AssetMetadataUpdated(DomainEvent):
    asset_path: Path | None = None
    asset_rel: str = ""
    gps: dict[str, float] = field(default_factory=dict)
    location: str = ""
    metadata_delta: dict[str, Any] = field(default_factory=dict)
    revision: int = 0
    source: str = "location_assignment"


@dataclass(frozen=True)
class LocationFileWriteVerified(DomainEvent):
    asset_path: Path | None = None
    gps: dict[str, float] = field(default_factory=dict)
    location: str = ""
    job_id: str = ""


@dataclass(frozen=True)
class LocationFileWriteFailed(DomainEvent):
    asset_path: Path | None = None
    gps: dict[str, float] = field(default_factory=dict)
    location: str = ""
    job_id: str = ""
    error: str = ""
    recoverable: bool = True


__all__ = [
    "AssetMetadataUpdated",
    "LocationFileWriteFailed",
    "LocationFileWriteVerified",
]
