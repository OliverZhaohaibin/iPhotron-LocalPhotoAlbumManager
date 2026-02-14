"""Pure-Python paginated asset loader.

Loads assets page-by-page via ``AssetService`` / ``IAssetRepository`` using
``AssetQuery.paginate()``, avoiding loading the entire dataset into memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Protocol

from iPhoto.domain.models.core import Asset
from iPhoto.domain.models.query import AssetQuery

LOGGER = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE: int = 200


class AssetFinder(Protocol):
    """Minimal protocol for the query side of an asset repository/service."""

    def find_assets(self, query: AssetQuery) -> List[Asset]: ...

    def count_assets(self, query: AssetQuery) -> int: ...


@dataclass
class PageResult:
    """Result of loading a single page."""

    items: List[Asset] = field(default_factory=list)
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    total_count: int = 0

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total_count

    @property
    def total_pages(self) -> int:
        if self.page_size <= 0 or self.total_count <= 0:
            return 0
        return (self.total_count + self.page_size - 1) // self.page_size


class PaginatedAssetLoader:
    """Stateful paginated asset loader.

    Maintains the current page and accumulated items.  Callers advance
    through pages via :meth:`load_page` / :meth:`load_next_page`.
    """

    def __init__(
        self,
        finder: AssetFinder,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self._finder = finder
        self._page_size = page_size

        # State
        self._items: List[Asset] = []
        self._current_page: int = 0
        self._total_count: int = 0
        self._base_query: Optional[AssetQuery] = None

    # -- properties --------------------------------------------------------

    @property
    def items(self) -> List[Asset]:
        return self._items

    @property
    def current_page(self) -> int:
        return self._current_page

    @property
    def page_size(self) -> int:
        return self._page_size

    @property
    def total_count(self) -> int:
        return self._total_count

    @property
    def has_more(self) -> bool:
        if self._current_page <= 0:
            return False
        return self._current_page * self._page_size < self._total_count

    @property
    def total_pages(self) -> int:
        if self._page_size <= 0 or self._total_count <= 0:
            return 0
        return (self._total_count + self._page_size - 1) // self._page_size

    # -- public API --------------------------------------------------------

    def reset(self, query: AssetQuery) -> PageResult:
        """Start a new query from page 1, replacing any previous state."""
        self._base_query = query
        self._items.clear()
        self._current_page = 0
        self._total_count = self._finder.count_assets(self._count_query(query))
        return self.load_next_page()

    def load_next_page(self) -> PageResult:
        """Load the next page of results and **append** to the item list."""
        if self._base_query is None:
            return PageResult()
        if self._current_page > 0 and not self.has_more:
            return PageResult(
                items=[],
                page=self._current_page,
                page_size=self._page_size,
                total_count=self._total_count,
            )
        return self.load_page(self._current_page + 1)

    def load_page(self, page: int) -> PageResult:
        """Load a specific page and **append** its items."""
        if self._base_query is None:
            return PageResult()

        query = AssetQuery(**self._base_query.__dict__)
        query.paginate(page, self._page_size)

        assets = self._finder.find_assets(query)
        self._items.extend(assets)
        self._current_page = page

        return PageResult(
            items=assets,
            page=page,
            page_size=self._page_size,
            total_count=self._total_count,
        )

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _count_query(query: AssetQuery) -> AssetQuery:
        """Clone the query with pagination stripped — for COUNT(*)."""
        q = AssetQuery(**query.__dict__)
        q.limit = None
        q.offset = 0
        return q
