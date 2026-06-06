from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ScanStage(str, Enum):
    DISCOVER = "discover"
    STAT_CACHE = "stat_cache_validation"
    METADATA = "metadata_extraction"
    THUMBNAIL = "thumbnail_extraction"
    DB_COMMIT = "db_commit"
    VISIBLE_PUBLISH = "visible_publish"
    DERIVED_JOBS = "derived_jobs_enqueue"


@dataclass(frozen=True)
class ScanJob:
    job_id: str
    root: str
    scope: str
    status: str
    stage: ScanStage
    found_count: int = 0
    processed_count: int = 0
    visible_count: int = 0
    failed_count: int = 0


@dataclass(frozen=True)
class ScanBatchCommitted:
    job_id: str
    root: Path
    collection_revision: int
    ready_count: int
    rows: list[dict[str, Any]]
    stage_elapsed_ms: dict[str, float] = field(default_factory=dict)
