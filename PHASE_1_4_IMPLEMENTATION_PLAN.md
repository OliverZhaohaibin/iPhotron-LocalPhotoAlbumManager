# Phase 1-4 Implementation Plan for asset_list_model.py Refactoring

## Overview

This document outlines the implementation plan for reducing `asset_list_model.py` from 1177 lines to under 300 lines by extracting additional components beyond the initial Phase 1 work.

## Current Status

### ✅ Completed: Initial Component Extraction

The following components have been created and are ready for integration:

1. **AssetStreamBuffer** (`streaming.py`) - 217 lines
   - Buffered chunk processing with throttling
   - Deduplication tracking
   - Timer-based flushing
   - Finish event handling

2. **OptimisticTransactionManager** (`transactions.py`) - 200 lines
   - Pending move tracking
   - Rollback state management
   - Optimistic UI updates

3. **ModelFilterHandler** (`filter_engine.py`) - 119 lines
   - Filter mode validation
   - In-memory row filtering
   - Database query parameter generation

### ✅ Completed: Additional Component Extraction

The following new components have been created (Phase 2-4):

4. **AssetDataOrchestrator** (`orchestrator.py`) - ~330 lines
   - Manages AssetDataLoader, LiveIngestWorker, Scanner lifecycle
   - Coordinates worker output through filters and streaming buffers
   - Handles signal connections and data deduplication
   - Feeds prepared data to model for insertion

5. **IncrementalUpdateHandler** (`refresh_handler.py`) - ~220 lines
   - Manages IncrementalRefreshWorker lifecycle
   - Uses ListDiffCalculator for diff computation
   - Signals model to perform beginInsertRows/beginRemoveRows
   - Isolates complex list diffing from Qt Model management

6. **AssetPathResolver** (`resolver.py`) - ~190 lines
   - Absolute path to relative path conversion
   - Metadata lookup by path
   - Fallback to recently removed cache
   - Path normalization and error handling

## Implementation Phases

### Phase 1: Component Integration (NOT YET DONE)

**Goal:** Replace legacy inline code with the new classes.

**Tasks:**
1. Instantiate all new components in `AssetListModel.__init__`:
   - `AssetStreamBuffer`
   - `OptimisticTransactionManager`
   - `ModelFilterHandler`
   - `AssetDataOrchestrator`
   - `IncrementalUpdateHandler`
   - `AssetPathResolver`

2. Remove legacy fields and methods:
   - `self._pending_chunks_buffer` → use `AssetStreamBuffer`
   - `self._flush_timer` → delegated to `AssetStreamBuffer`
   - `self._pending_rels`, `self._pending_abs` → handled by `AssetStreamBuffer`
   - Complex streaming logic in `_on_loader_chunk_ready` → use `AssetDataOrchestrator`
   - `update_rows_for_move` → delegate to `OptimisticTransactionManager`

3. Redirect calls to new components:
   ```python
   # Old:
   self._pending_chunks_buffer.extend(chunk)
   
   # New:
   self._stream_buffer.add_chunk(chunk, existing_rels, abs_lookup)
   ```

**Expected Line Reduction:** ~200-300 lines

### Phase 2: Data Loading Orchestration (PARTIALLY DONE)

**Goal:** Remove signal handling boilerplate from model.

**Tasks:**
1. Connect `AssetDataOrchestrator` signals to model:
   ```python
   self._orchestrator.firstChunkReady.connect(self._handle_first_chunk)
   self._orchestrator.rowsReadyForInsertion.connect(self._insert_batch)
   self._orchestrator.loadProgress.connect(self.loadProgress.emit)
   self._orchestrator.loadFinished.connect(self._on_load_finished)
   ```

2. Remove direct signal connections:
   - `self._data_loader.chunkReady.connect(...)` → handled by orchestrator
   - `self._data_loader.loadProgress.connect(...)` → forwarded by orchestrator
   - `self._data_loader.loadFinished.connect(...)` → forwarded by orchestrator

3. Simplify load method:
   ```python
   def load(self, album_root, featured, filter_params=None):
       self._orchestrator.start_load(
           album_root, featured, filter_params, self._facade.library_manager
       )
   ```

**Expected Line Reduction:** ~150-200 lines

### Phase 3: Incremental Refresh Management (PARTIALLY DONE)

**Goal:** Isolate diff & patch logic.

**Tasks:**
1. Connect `IncrementalUpdateHandler` signals:
   ```python
   self._refresh_handler.removeRowsRequested.connect(self._remove_rows_at)
   self._refresh_handler.insertRowsRequested.connect(self._insert_rows_at)
   self._refresh_handler.rowDataChanged.connect(self._update_row_data)
   self._refresh_handler.modelResetRequested.connect(self._full_reset)
   ```

2. Remove methods:
   - `_refresh_rows_from_index` → use `refresh_handler.refresh_from_index()`
   - `_apply_incremental_results` → handled by `IncrementalUpdateHandler`
   - `_cleanup_incremental_worker` → handled by `IncrementalUpdateHandler`
   - Complex diff logic → delegated to handler

3. Simplify refresh calls:
   ```python
   # Old:
   self._refresh_rows_from_index(self._album_root, descendant_root)
   
   # New:
   self._refresh_handler.refresh_from_index(self._album_root, descendant_root)
   ```

**Expected Line Reduction:** ~200-250 lines

### Phase 4: Path & Metadata Resolution (DONE)

**Goal:** Cleaner data access layer.

**Tasks:**
1. Instantiate `AssetPathResolver`:
   ```python
   self._path_resolver = AssetPathResolver(
       get_rows=lambda: self._state_manager.rows,
       get_row_lookup=lambda: self._state_manager.row_lookup,
       get_abs_lookup=self._state_manager.get_index_by_abs,
       get_recently_removed=self._cache_manager.get_recently_removed,
       album_root_getter=lambda: self._album_root,
   )
   ```

2. Replace `metadata_for_absolute_path` implementation:
   ```python
   # Old: ~40 lines of path resolution logic
   
   # New:
   def metadata_for_absolute_path(self, path: Path):
       return self._path_resolver.metadata_for_absolute_path(path)
   ```

**Expected Line Reduction:** ~50-80 lines

## Projected Results

### Before Refactoring
- **asset_list_model.py**: 1177 lines
- Responsibilities: Everything
- Testability: Difficult (requires full Qt setup)

### After Phase 1-4
- **asset_list_model.py**: ~250-300 lines (target: <300)
- Responsibilities: 
  - Qt Model interface implementation
  - Basic row management (delegate to `_state_manager`)
  - Signal coordination
- Testability: Much easier

### Component Distribution
```
asset_list/
├── __init__.py                 (30 lines)
├── streaming.py               (217 lines) ✅ Created
├── transactions.py            (200 lines) ✅ Created
├── filter_engine.py           (119 lines) ✅ Created
├── orchestrator.py            (330 lines) ✅ Created
├── refresh_handler.py         (220 lines) ✅ Created
└── resolver.py                (190 lines) ✅ Created

Total extracted: ~1,306 lines
Remaining in model: ~250-300 lines
```

## Benefits

1. **Testability**
   - Each component can be unit tested independently
   - No need for full Qt application setup
   - Mock-friendly interfaces

2. **Maintainability**
   - Clear module boundaries
   - Single responsibility per class
   - Easy to locate and fix bugs

3. **Reusability**
   - Components can be used in other models
   - `AssetDataOrchestrator` could serve multiple views
   - `AssetPathResolver` useful for any path-based operations

4. **Performance**
   - Streaming buffer prevents UI freezing
   - Optimistic transactions improve perceived performance
   - Incremental updates more efficient than full reloads

## Next Steps for Integration

1. **Create integration branch**
   - Start with Phase 1 integration
   - Test each phase incrementally

2. **Update tests**
   - Modify existing model tests to use new structure
   - Add unit tests for each new component
   - Verify all functionality preserved

3. **Documentation**
   - Update model docstrings
   - Add usage examples for each component
   - Create migration guide for developers

4. **Performance validation**
   - Benchmark loading times
   - Verify UI responsiveness
   - Test with large albums (10k+ assets)

## Implementation Notes

### Component Dependencies

The components have the following dependency relationships:

```
AssetListModel (thin shell)
    ├─> AssetDataOrchestrator
    │   ├─> AssetDataLoader (existing)
    │   ├─> ModelFilterHandler
    │   └─> AssetStreamBuffer
    │
    ├─> IncrementalUpdateHandler
    │   ├─> IncrementalRefreshWorker (existing)
    │   └─> ListDiffCalculator (existing)
    │
    ├─> AssetPathResolver
    │   └─> (callbacks to state manager)
    │
    ├─> OptimisticTransactionManager
    └─> AssetStateManager (existing)
```

### Callback Pattern

Many components use callbacks instead of direct dependencies to maintain loose coupling:

```python
# Good: Callback pattern
resolver = AssetPathResolver(
    get_rows=lambda: self._state_manager.rows,
    ...
)

# Bad: Direct dependency
resolver = AssetPathResolver(state_manager)
```

This allows:
- Easier testing (mock the callbacks)
- Flexibility in data sources
- No circular dependencies

### Signal Flow

```
AssetDataLoader.chunkReady
    → AssetDataOrchestrator._on_loader_chunk_ready
        → ModelFilterHandler.filter_rows
        → AssetStreamBuffer.add_chunk
        → AssetStreamBuffer._on_timer_flush
    → AssetDataOrchestrator.rowsReadyForInsertion
→ AssetListModel._insert_batch
```

## Conclusion

The component extraction is complete. The next step is to integrate these components into `asset_list_model.py` to achieve the target of <300 lines. This will require:

1. Careful refactoring of the model's `__init__` method
2. Rewiring signal connections
3. Removing redundant code
4. Extensive testing to ensure no regressions

The architecture is now in place to support a clean, maintainable, and testable model implementation.
