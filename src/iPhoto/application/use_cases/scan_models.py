"""Shared scan lifecycle models for application, runtime, and GUI layers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ScanMode(str, Enum):
    """Execution mode for one scan lifecycle."""

    ATOMIC = "atomic"
    BACKGROUND = "background"
    INITIAL_SAFE = "initial_safe"


class ScanPressureLevel(str, Enum):
    """Resource pressure level for one scan lifecycle."""

    NORMAL = "normal"
    CONSTRAINED = "constrained"
    CRITICAL = "critical"


class ScanScopeKind(str, Enum):
    """High-level scope category for one scan lifecycle."""

    LIBRARY_ROOT = "library_root"
    ALBUM_SUBTREE = "album_subtree"
    REPAIR_IMPORT = "repair_import"


class ScanProgressPhase(str, Enum):
    """High-level phases surfaced to transport and UI layers."""

    DISCOVERING = "discovering"
    INDEXING = "indexing"
    DEFERRED_PAIRING = "deferred_pairing"
    PAUSED_FOR_MEMORY = "paused_for_memory"
    CANCELLED_RESUMABLE = "cancelled_resumable"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ScanPlan:
    """Decision-complete execution plan for one scan."""

    root: Path
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    mode: ScanMode
    scope_kind: ScanScopeKind
    scan_id: str
    persist_chunks: bool
    collect_rows: bool
    safe_mode: bool
    estimated_asset_count: int | None
    pressure_level: ScanPressureLevel
    degrade_reason: str | None
    generate_micro_thumbnails: bool
    allow_face_scan: bool
    defer_live_pairing: bool
    resumed_from_scan_id: str | None = None


@dataclass(frozen=True)
class ScanStatusUpdate:
    """Progress/status snapshot emitted during scanning."""

    root: Path
    scan_id: str
    mode: ScanMode
    phase: ScanProgressPhase
    pressure_level: ScanPressureLevel = ScanPressureLevel.NORMAL
    processed: int = 0
    total: int | None = None
    failed_count: int = 0
    deferred_face_scan: bool = False
    deferred_pairing_reason: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class ScanCompletion:
    """Final scan outcome handed to runtime/service adapters."""

    root: Path
    scan_id: str
    mode: ScanMode
    processed_count: int
    pressure_level: ScanPressureLevel = ScanPressureLevel.NORMAL
    failed_count: int = 0
    success: bool = True
    cancelled: bool = False
    safe_mode: bool = False
    defer_live_pairing: bool = False
    deferred_face_scan: bool = False
    degrade_reason: str | None = None
    deferred_pairing_reason: str | None = None
    allow_face_scan: bool = True
    phase: ScanProgressPhase = ScanProgressPhase.COMPLETED
