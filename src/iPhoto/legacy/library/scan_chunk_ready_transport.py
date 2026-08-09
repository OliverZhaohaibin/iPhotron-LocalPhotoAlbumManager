"""Reference-only archive of the removed scanChunkReady transport.

This module is intentionally inert. Production code must not import or call it;
it exists only to preserve the pre-removal shape while fixing bugs that may need
historical context.
"""

REMOVED_SCAN_CHUNK_READY_TRANSPORT = r'''
class ScannerSignals(QObject):
    progressUpdated = Signal(Path, int, int)
    chunkReady = Signal(Path, list)
    batchCommitted = Signal(object)
    finished = Signal(Path, list)
    error = Signal(Path, str)
    batchFailed = Signal(Path, int)


class ScannerWorker(QRunnable):
    def _process_chunk(self, store, chunk):
        self._failed_count += merge_scan_chunk_with_repository(
            store,
            root=self._root,
            include=self._include,
            exclude=self._exclude,
            chunk=chunk,
            chunk_callback=lambda emitted: self._signals.chunkReady.emit(
                self._root,
                emitted,
            ),
            batch_failed_callback=lambda count: self._signals.batchFailed.emit(
                self._root,
                count,
            ),
        )

    def _emit_chunk_if_active(self, chunk):
        if self._is_cancelled:
            return
        self._signals.chunkReady.emit(self._root, chunk)


class ScanCoordinatorMixin:
    def _on_scan_chunk(self, root, chunk):
        if not chunk:
            return
        self.invalidate_geotagged_assets_cache()
        if self._current_face_scanner is not None:
            self._current_face_scanner.enqueue_rows(chunk)
        self.scanChunkReady.emit(root, chunk)


class AppFacade(QObject):
    scanChunkReady = Signal(Path, list)

    def _relay_scan_chunk_ready(self, root, chunk):
        self.scanChunkReady.emit(root, chunk)
'''
