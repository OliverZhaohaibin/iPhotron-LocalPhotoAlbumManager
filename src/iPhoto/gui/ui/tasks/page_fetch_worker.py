from __future__ import annotations

from typing import List

from PySide6.QtCore import QObject, QRunnable, Signal

from ..models.data_source import AssetDataSource


class PageFetchSignals(QObject):
    results_ready = Signal(list)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class PageFetchWorker(QRunnable):
    """Background worker that fetches a single page from an AssetDataSource."""

    def __init__(self, source: AssetDataSource, limit: int) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._source = source
        self._limit = limit
        self.signals = PageFetchSignals()

    def run(self) -> None:  # pragma: no cover - runs in background thread
        try:
            items: List[dict] = self._source.fetch_next(self._limit)
        except Exception:
            items = []
        self.signals.results_ready.emit(items)
