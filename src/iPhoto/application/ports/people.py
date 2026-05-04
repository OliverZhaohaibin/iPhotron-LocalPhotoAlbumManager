"""People bounded-context ports."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, Protocol


class PeopleAssetRepositoryPort(Protocol):
    """Asset-index boundary used by the People bounded context."""

    def get_rows_by_ids(self, asset_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Return asset rows keyed by asset id."""

    def read_rows_by_face_status(
        self,
        statuses: Iterable[str],
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield asset rows whose face status is in *statuses*."""

    def update_face_status(self, asset_id: str, status: str) -> None:
        """Persist the face status for one asset row."""

    def update_face_statuses(self, asset_ids: Iterable[str], status: str) -> None:
        """Persist the same face status for many asset rows."""

    def count_by_face_status(self) -> dict[str, int]:
        """Return face-status counts from the asset index."""


class PeopleIndexPort(Protocol):
    """Application boundary for People runtime and stable state."""

    def enqueue_assets(self, rows: list[dict[str, Any]]) -> None:
        """Queue face-eligible asset rows for People processing."""

    def commit_runtime_snapshot(self, snapshot: Any) -> None:
        """Persist a rebuildable People runtime snapshot."""
