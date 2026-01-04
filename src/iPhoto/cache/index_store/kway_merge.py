"""K-Way Merge Provider for efficient aggregation of multiple sorted data sources.

This module provides a streaming merge algorithm that combines assets from multiple
albums/sources while maintaining sort order and enabling lazy loading. This is
critical for the "All Photos" view performance.

Architecture:
    The K-Way Merge uses a min-heap (priority queue) to efficiently merge k sorted
    iterators. This reduces the complexity from O(n log n) (global sort) to
    O(n log k), where k is the number of source albums.

Performance:
    - Time Complexity: O(n log k) where n = total items, k = number of sources
    - Space Complexity: O(k) for the heap + O(1) per yielded item
    - Memory: Items are yielded lazily, so memory usage doesn't scale with n

Usage:
    >>> from iPhoto.cache.index_store.kway_merge import KWayMergeProvider, AssetIterator
    >>> 
    >>> # Create iterators for each album
    >>> iterators = [
    ...     AssetIterator(album_path="2023/Trip", repository=repo),
    ...     AssetIterator(album_path="2023/Family", repository=repo),
    ... ]
    >>> 
    >>> # Create merge provider
    >>> provider = KWayMergeProvider(iterators, page_size=50)
    >>> 
    >>> # Lazily iterate through all items in sorted order
    >>> for asset in provider:
    ...     display(asset)
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .repository import AssetRepository


@dataclass
class AssetIterator:
    """Iterator wrapper for a single album source with cursor-based pagination.
    
    This class provides a lazy iterator over assets from a single album,
    fetching pages on demand using cursor-based pagination.
    
    Attributes:
        album_path: The album path to fetch assets from.
        repository: The AssetRepository to use for fetching.
        page_size: Number of items to fetch per page (default: 100).
        include_subalbums: Whether to include sub-albums (default: True).
        filter_hidden: Whether to exclude hidden assets (default: True).
        filter_params: Additional filter parameters.
    """
    album_path: Optional[str]
    repository: "AssetRepository"
    page_size: int = 100
    include_subalbums: bool = True
    filter_hidden: bool = True
    filter_params: Optional[Dict[str, Any]] = None
    
    _cursor: Optional[str] = field(default=None, init=False, repr=False)
    _buffer: List[Dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _exhausted: bool = field(default=False, init=False, repr=False)
    _position: int = field(default=0, init=False, repr=False)
    
    def __iter__(self) -> Iterator[Dict[str, Any]]:
        """Return self as an iterator."""
        return self
    
    def __next__(self) -> Dict[str, Any]:
        """Return the next asset from this source.
        
        Raises:
            StopIteration: When all items have been yielded.
        """
        # Refill buffer if empty
        if self._position >= len(self._buffer):
            if self._exhausted:
                raise StopIteration
            self._fetch_next_page()
            if not self._buffer:
                raise StopIteration
        
        item = self._buffer[self._position]
        self._position += 1
        return item
    
    def _fetch_next_page(self) -> None:
        """Fetch the next page of assets using cursor pagination."""
        page = self.repository.fetch_by_cursor(
            cursor=self._cursor,
            limit=self.page_size,
            album_path=self.album_path,
            include_subalbums=self.include_subalbums,
            filter_hidden=self.filter_hidden,
            filter_params=self.filter_params,
        )
        
        self._buffer = page.items
        self._position = 0
        self._cursor = page.next_cursor
        self._exhausted = not page.has_more
    
    def peek(self) -> Optional[Dict[str, Any]]:
        """Return the next item without consuming it.
        
        Returns:
            The next asset dictionary, or None if exhausted.
        """
        if self._position >= len(self._buffer):
            if self._exhausted:
                return None
            self._fetch_next_page()
            if not self._buffer:
                return None
        return self._buffer[self._position]
    
    def reset(self) -> None:
        """Reset the iterator to start from the beginning."""
        self._cursor = None
        self._buffer = []
        self._exhausted = False
        self._position = 0


def _sort_key(item: Dict[str, Any]) -> Tuple[str, str]:
    """Extract the sort key from an asset for comparison.
    
    Uses (dt DESC, id DESC) ordering to match the database sort order.
    We negate the values conceptually by returning them as-is since heapq
    is a min-heap - items with "larger" dt values should come first.
    
    Args:
        item: Asset dictionary.
        
    Returns:
        A tuple (dt, id) for sorting. Missing values are handled gracefully.
    """
    dt = item.get("dt") or ""
    item_id = item.get("id") or ""
    return (dt, item_id)


@dataclass
class _HeapEntry:
    """Wrapper for heap entries that supports reverse comparison.
    
    Since heapq is a min-heap but we want descending order (newest first),
    we invert the comparison operators.
    """
    sort_key: Tuple[str, str]
    source_index: int
    item: Dict[str, Any]
    
    def __lt__(self, other: "_HeapEntry") -> bool:
        # Reverse comparison for descending order
        # Larger dt values should come first
        return self.sort_key > other.sort_key
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _HeapEntry):
            return NotImplemented
        return self.sort_key == other.sort_key


class KWayMergeProvider:
    """Efficiently merges multiple sorted asset streams using a min-heap.
    
    This class implements the K-Way Merge algorithm to combine assets from
    multiple sources (albums) while maintaining the global sort order
    (by creation date descending).
    
    The merge is performed lazily - assets are only fetched from the underlying
    sources as needed, which minimizes memory usage and allows the first
    viewport to render almost instantly.
    
    Attributes:
        sources: List of AssetIterator instances to merge.
        page_size: Number of items to yield per batch (for optional batching).
    
    Example:
        >>> provider = KWayMergeProvider([
        ...     AssetIterator("Album1", repo),
        ...     AssetIterator("Album2", repo),
        ...     AssetIterator("Album3", repo),
        ... ])
        >>> for asset in provider:
        ...     print(asset["rel"])
    """
    
    def __init__(
        self,
        sources: List[AssetIterator],
        page_size: int = 100,
    ):
        """Initialize the K-Way Merge Provider.
        
        Args:
            sources: List of AssetIterator instances to merge.
            page_size: Number of items per page for batched iteration.
        """
        self._sources = sources
        self._page_size = page_size
        self._heap: List[_HeapEntry] = []
        self._initialized = False
        self._exhausted = False
    
    def _initialize_heap(self) -> None:
        """Initialize the heap with the first item from each source."""
        if self._initialized:
            return
        
        self._heap = []
        for idx, source in enumerate(self._sources):
            item = source.peek()
            if item is not None:
                entry = _HeapEntry(
                    sort_key=_sort_key(item),
                    source_index=idx,
                    item=item,
                )
                heapq.heappush(self._heap, entry)
        
        self._initialized = True
    
    def __iter__(self) -> Iterator[Dict[str, Any]]:
        """Return self as an iterator."""
        return self
    
    def __next__(self) -> Dict[str, Any]:
        """Return the next asset in global sorted order.
        
        Raises:
            StopIteration: When all sources are exhausted.
        """
        if not self._initialized:
            self._initialize_heap()
        
        if not self._heap:
            raise StopIteration
        
        # Pop the smallest (actually largest dt due to reverse comparison)
        entry = heapq.heappop(self._heap)
        result = entry.item
        source_idx = entry.source_index
        
        # Advance the source and re-add to heap if not exhausted
        source = self._sources[source_idx]
        try:
            next(source)  # Consume the item we just yielded
            next_item = source.peek()
            if next_item is not None:
                new_entry = _HeapEntry(
                    sort_key=_sort_key(next_item),
                    source_index=source_idx,
                    item=next_item,
                )
                heapq.heappush(self._heap, new_entry)
        except StopIteration:
            pass  # Source is exhausted, don't add back to heap
        
        return result
    
    def fetch_page(self) -> List[Dict[str, Any]]:
        """Fetch a page of items for UI consumption.
        
        Returns:
            A list of up to page_size assets in sorted order.
        """
        results = []
        for _ in range(self._page_size):
            try:
                item = next(self)
                results.append(item)
            except StopIteration:
                break
        return results
    
    def has_more(self) -> bool:
        """Check if there are more items available.
        
        Returns:
            True if more items can be fetched, False otherwise.
        """
        if not self._initialized:
            self._initialize_heap()
        return bool(self._heap)
    
    def reset(self) -> None:
        """Reset the merge provider to start from the beginning.
        
        This resets all underlying source iterators as well.
        """
        for source in self._sources:
            source.reset()
        self._heap = []
        self._initialized = False
        self._exhausted = False


def create_all_photos_provider(
    repository: "AssetRepository",
    album_paths: Optional[List[str]] = None,
    page_size: int = 100,
    filter_hidden: bool = True,
    filter_params: Optional[Dict[str, Any]] = None,
) -> KWayMergeProvider:
    """Create a KWayMergeProvider for the "All Photos" view.
    
    This is a convenience factory function that creates iterators for each
    album and combines them into a single merge provider.
    
    Args:
        repository: The AssetRepository to use.
        album_paths: List of album paths to include. If None, fetches all albums.
        page_size: Number of items per page.
        filter_hidden: Whether to exclude hidden assets.
        filter_params: Additional filter parameters.
    
    Returns:
        A configured KWayMergeProvider instance.
    
    Example:
        >>> provider = create_all_photos_provider(repo)
        >>> for asset in provider:
        ...     display(asset)
    """
    # If no album paths specified, get all albums from repository
    if album_paths is None:
        album_paths = repository.list_albums()
    
    # If still empty or just one album, use a single iterator for all
    if not album_paths:
        # Single iterator for all photos (no album filter)
        sources = [
            AssetIterator(
                album_path=None,
                repository=repository,
                page_size=page_size,
                include_subalbums=True,
                filter_hidden=filter_hidden,
                filter_params=filter_params,
            )
        ]
    elif len(album_paths) == 1:
        # Single album, single iterator with subalbums
        sources = [
            AssetIterator(
                album_path=album_paths[0],
                repository=repository,
                page_size=page_size,
                include_subalbums=True,
                filter_hidden=filter_hidden,
                filter_params=filter_params,
            )
        ]
    else:
        # Multiple albums - create iterator for each
        sources = [
            AssetIterator(
                album_path=path,
                repository=repository,
                page_size=page_size,
                include_subalbums=True,
                filter_hidden=filter_hidden,
                filter_params=filter_params,
            )
            for path in album_paths
        ]
    
    return KWayMergeProvider(sources, page_size=page_size)
