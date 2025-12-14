        # Implementing path re-basing:
        try:
            scan_root = root.resolve()
            view_root = self._album_root.resolve()
        except OSError as exc:
            logger.warning("Failed to resolve paths during scan chunk processing: %s", exc)
            return

        is_direct_match = (scan_root == view_root)
        is_scan_parent_of_view = (scan_root in view_root.parents)
