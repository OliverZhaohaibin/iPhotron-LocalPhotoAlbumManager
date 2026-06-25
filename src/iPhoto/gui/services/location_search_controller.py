"""Asynchronous location search controller for the info panel editor."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from maps.osmand_search import OsmAndSearchService, SearchSuggestion

_LOCATION_SEARCH_RESULT_LIMIT = 5


class _LocationSearchSignals(QObject):
    ready = Signal(int, object, str, object)
    error = Signal(int, object, str, str)
    finished = Signal(int)


class _LocationSearchWorker(QRunnable):
    def __init__(
        self,
        *,
        token: int,
        target_path: Path,
        query: str,
        package_root: Path | None,
        locale: str,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.signals = _LocationSearchSignals()
        self._token = int(token)
        self._target_path = Path(target_path)
        self._query = str(query)
        self._package_root = Path(package_root) if package_root is not None else None
        self._locale = str(locale or "")

    def run(self) -> None:  # pragma: no cover - exercised via controller tests
        try:
            service = OsmAndSearchService(package_root=self._package_root)
            try:
                suggestions = service.search(
                    self._query,
                    limit=_LOCATION_SEARCH_RESULT_LIMIT,
                    locale=self._locale,
                )
            finally:
                service.shutdown()
            self.signals.ready.emit(
                self._token,
                self._target_path,
                self._query,
                suggestions,
            )
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(
                self._token,
                self._target_path,
                self._query,
                str(exc),
            )
        finally:
            self.signals.finished.emit(self._token)


class LocationSearchController(QObject):
    """Debounced caller-facing wrapper around off-GUI-thread location search."""

    suggestionsReady = Signal(int, object, str, object)
    searchFailed = Signal(int, object, str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._token = 0
        self._active_tokens: set[int] = set()
        self._cache: dict[str, list[SearchSuggestion]] = {}
        self._target_path: Path | None = None

    def reset(self) -> None:
        self._token += 1
        self._active_tokens.clear()
        self._target_path = None
        self._pool.clear()

    def shutdown(self) -> None:
        self.reset()
        self._pool.waitForDone(500)

    def clear_cache(self) -> None:
        self._cache.clear()

    def search(
        self,
        query: str,
        *,
        target_path: Path,
        package_root: Path | None,
        locale: str,
    ) -> int:
        self._token += 1
        token = self._token
        self._target_path = Path(target_path)
        trimmed = query.strip()
        if not self._should_search(trimmed):
            self.suggestionsReady.emit(token, self._target_path, trimmed, [])
            return token

        normalized_query = self._normalize_query(trimmed)
        exact = self._cache.get(normalized_query)
        if exact is not None:
            self.suggestionsReady.emit(token, self._target_path, trimmed, list(exact))
            return token

        preview = self._preview_cached(trimmed)
        if preview is not None:
            self.suggestionsReady.emit(token, self._target_path, trimmed, preview)

        self._pool.clear()
        self._active_tokens.add(token)
        worker = _LocationSearchWorker(
            token=token,
            target_path=self._target_path,
            query=trimmed,
            package_root=package_root,
            locale=locale,
        )
        worker.signals.ready.connect(self._handle_ready)
        worker.signals.error.connect(self._handle_error)
        worker.signals.finished.connect(self._handle_finished)
        self._pool.start(worker)
        return token

    def _handle_ready(
        self,
        token: int,
        target_path: object,
        query: str,
        suggestions_obj: object,
    ) -> None:
        if token != self._token or Path(target_path) != self._target_path:
            return
        suggestions = list(suggestions_obj) if isinstance(suggestions_obj, list) else []
        self._cache[self._normalize_query(query)] = suggestions
        if len(self._cache) > 64:
            self._cache.pop(next(iter(self._cache)), None)
        self.suggestionsReady.emit(token, target_path, query, suggestions)

    def _handle_error(
        self,
        token: int,
        target_path: object,
        query: str,
        message: str,
    ) -> None:
        if token != self._token or Path(target_path) != self._target_path:
            return
        self.searchFailed.emit(token, target_path, query, message)

    def _handle_finished(self, token: int) -> None:
        self._active_tokens.discard(int(token))

    def _preview_cached(self, query: str) -> list[SearchSuggestion] | None:
        normalized_query = self._normalize_query(query)
        for cached_query, cached_results in sorted(
            self._cache.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if not normalized_query.startswith(cached_query):
                continue
            filtered = [
                suggestion
                for suggestion in cached_results
                if normalized_query in self._normalize_query(
                    " ".join(
                        part
                        for part in (
                            suggestion.display_name,
                            suggestion.secondary_text,
                        )
                        if part
                    )
                )
            ]
            if filtered:
                return filtered[:_LOCATION_SEARCH_RESULT_LIMIT]
        return None

    @staticmethod
    def _normalize_query(query: str) -> str:
        return " ".join(query.split()).casefold()

    @staticmethod
    def _should_search(query: str) -> bool:
        trimmed = " ".join(query.split())
        if not trimmed:
            return False
        if len(trimmed) >= 2:
            return True
        return any(ord(character) >= 128 for character in trimmed)


__all__ = ["LocationSearchController"]
