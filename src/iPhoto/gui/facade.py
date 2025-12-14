    def bind_library(self, library: "LibraryManager") -> None:
        """Remember the library manager so static collections stay in sync."""

        if self._library_manager is not None:
            try:
                self._library_manager.treeUpdated.disconnect(self._on_library_tree_updated)
                self._library_manager.scanProgress.disconnect(self._relay_scan_progress)
                self._library_manager.scanChunkReady.disconnect(self._relay_scan_chunk_ready)
                self._library_manager.scanFinished.disconnect(self._relay_scan_finished)
            except (RuntimeError, TypeError):
                # Ignore errors if signals were not connected or object is deleted
                pass

        self._library_manager = library
        self._library_update_service.reset_cache()
        self._library_manager.treeUpdated.connect(self._on_library_tree_updated)

        # Hook up scanning signals from LibraryManager to Facade
        self._library_manager.scanProgress.connect(self._relay_scan_progress)
        self._library_manager.scanChunkReady.connect(self._relay_scan_chunk_ready)
        self._library_manager.scanFinished.connect(self._relay_scan_finished)

        if self._library_manager.root():
            self._on_library_tree_updated()
