"""People bounded-context ports."""

from __future__ import annotations

from typing import Any, Protocol


class PeopleIndexPort(Protocol):
    """Application boundary for People runtime and stable state."""

    def enqueue_assets(self, rows: list[dict[str, Any]]) -> None:
        """Queue face-eligible asset rows for People processing."""

    def commit_runtime_snapshot(self, snapshot: Any) -> None:
        """Persist a rebuildable People runtime snapshot."""
