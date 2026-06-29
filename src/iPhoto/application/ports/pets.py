"""Pets bounded-context ports."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, Protocol


class PetAssetRepositoryPort(Protocol):
    """Asset-index boundary used by the Pets bounded context."""

    def get_rows_by_ids(self, asset_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Return asset rows keyed by asset id."""

    def read_rows_by_pet_status(
        self,
        statuses: Iterable[str],
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield asset rows whose pet status is in *statuses*."""

    def update_pet_status(self, asset_id: str, status: str) -> None:
        """Persist the pet status for one asset row."""

    def update_pet_statuses(self, asset_ids: Iterable[str], status: str) -> None:
        """Persist the same pet status for many asset rows."""

    def count_by_pet_status(self) -> dict[str, int]:
        """Return pet-status counts from the asset index."""


class PetIndexPort(Protocol):
    """Application boundary for Pets runtime and stable state."""

    def enqueue_assets(self, rows: list[dict[str, Any]]) -> None:
        """Queue pet-eligible asset rows for Pets processing."""

    def commit_runtime_snapshot(self, snapshot: Any) -> None:
        """Persist a rebuildable Pets runtime snapshot."""
