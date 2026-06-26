"""Asynchronous location search controller for the info panel editor."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QMetaObject, QObject, QThread, QTimer, Qt, Signal, Slot

from maps.osmand_search import OsmAndSearchService, SearchSuggestion

_LOCATION_SEARCH_RESULT_LIMIT = 5
_LOCATION_SEARCH_DEBOUNCE_MS = 50
_LOCATION_SEARCH_CACHE_LIMIT = 128


class _LocationSearchWorker(QObject):
    ready = Signal(int, object, str, object)
    error = Signal(int, object, str, str)

    def __init__(self) -> None:
        super().__init__()
        self._service: OsmAndSearchService | None = None
        self._package_root: Path | None = None

    @Slot(object, str)
    def warm_up(self, package_root_obj: object, locale: str) -> None:
        del locale
        try:
            self._ensure_service(package_root_obj)
        except Exception:
            # Warm-up is opportunistic; the foreground search path reports errors.
            return

    @Slot(int, object, str, object, str)
    def search(
        self,
        token: int,
        target_path: object,
        query: str,
        package_root_obj: object,
        locale: str,
    ) -> None:
        try:
            service = self._ensure_service(package_root_obj)
            suggestions = service.search(
                query,
                limit=_LOCATION_SEARCH_RESULT_LIMIT,
                locale=locale,
                fallback_on_empty=False,
            )
            self.ready.emit(token, target_path, query, suggestions)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(token, target_path, query, str(exc))

    @Slot()
    def shutdown(self) -> None:
        if self._service is None:
            return
        self._service.shutdown()
        self._service = None
        self._package_root = None

    def _ensure_service(self, package_root_obj: object) -> OsmAndSearchService:
        package_root = self._normalize_package_root(package_root_obj)
        if self._service is not None and package_root == self._package_root:
            return self._service
        self.shutdown()
        self._service = OsmAndSearchService(package_root=package_root)
        self._package_root = package_root
        return self._service

    @staticmethod
    def _normalize_package_root(package_root_obj: object) -> Path | None:
        if package_root_obj is None:
            return None
        try:
            return Path(package_root_obj).resolve()
        except TypeError:
            return None


class LocationSearchController(QObject):
    """Debounced caller-facing wrapper around off-GUI-thread location search."""

    suggestionsReady = Signal(int, object, str, object)
    searchFailed = Signal(int, object, str, str)

    _searchRequested = Signal(int, object, str, object, str)
    _warmRequested = Signal(object, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._token = 0
        self._target_path: Path | None = None
        self._pending_search: tuple[int, Path, str, Path | None, str] | None = None
        self._cache: OrderedDict[str, list[SearchSuggestion]] = OrderedDict()
        self._cache_package_root: Path | None = None

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_LOCATION_SEARCH_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._execute_pending_search)

        self._thread = QThread(self)
        self._worker = _LocationSearchWorker()
        self._worker.moveToThread(self._thread)
        self._searchRequested.connect(self._worker.search)
        self._warmRequested.connect(self._worker.warm_up)
        self._worker.ready.connect(self._handle_ready)
        self._worker.error.connect(self._handle_error)
        self._thread.start()

    def warm_up(
        self,
        *,
        package_root: Path | None,
        locale: str,
    ) -> None:
        normalized_root = self._normalize_package_root(package_root)
        self._set_cache_package_root(normalized_root)
        self._warmRequested.emit(normalized_root, str(locale or ""))

    def reset(self) -> None:
        self._token += 1
        self._target_path = None
        self._pending_search = None
        self._debounce_timer.stop()

    def shutdown(self) -> None:
        self.reset()
        self.clear_cache()
        if self._thread.isRunning():
            try:
                QMetaObject.invokeMethod(
                    self._worker,
                    "shutdown",
                    Qt.ConnectionType.BlockingQueuedConnection,
                )
            except RuntimeError:
                self._worker.shutdown()
            self._thread.quit()
            self._thread.wait(1000)
        else:
            self._worker.shutdown()

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
        self._pending_search = None
        self._debounce_timer.stop()

        normalized_root = self._normalize_package_root(package_root)
        self._set_cache_package_root(normalized_root)
        trimmed = query.strip()
        if not self._should_search(trimmed):
            self.suggestionsReady.emit(token, self._target_path, trimmed, [])
            return token

        normalized_query = self._normalize_query(trimmed)
        exact = self._cache.get(normalized_query)
        if exact is not None:
            self._cache.move_to_end(normalized_query)
            self.suggestionsReady.emit(token, self._target_path, trimmed, list(exact))
            return token

        preview = self._preview_cached(trimmed)
        self.suggestionsReady.emit(
            token,
            self._target_path,
            trimmed,
            preview if preview is not None else [],
        )
        self._pending_search = (
            token,
            self._target_path,
            trimmed,
            normalized_root,
            str(locale or ""),
        )
        self._debounce_timer.start()
        return token

    @Slot()
    def _execute_pending_search(self) -> None:
        pending = self._pending_search
        self._pending_search = None
        if pending is None:
            return
        token, target_path, query, package_root, locale = pending
        if token != self._token or target_path != self._target_path:
            return
        self._searchRequested.emit(token, target_path, query, package_root, locale)

    @Slot(int, object, str, object)
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
        normalized_query = self._normalize_query(query)
        self._cache[normalized_query] = suggestions
        self._cache.move_to_end(normalized_query)
        while len(self._cache) > _LOCATION_SEARCH_CACHE_LIMIT:
            self._cache.popitem(last=False)
        self.suggestionsReady.emit(token, target_path, query, suggestions)

    @Slot(int, object, str, str)
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

    def _set_cache_package_root(self, package_root: Path | None) -> None:
        if package_root == self._cache_package_root:
            return
        self._cache_package_root = package_root
        self.clear_cache()

    @staticmethod
    def _normalize_package_root(package_root: Path | None) -> Path | None:
        if package_root is None:
            return None
        return Path(package_root).resolve()

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
