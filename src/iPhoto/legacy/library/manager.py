"""Compatibility shim for the quarantined legacy LibraryManager import."""

from __future__ import annotations

import warnings

from iPhoto.library.runtime_controller import GeotaggedAsset, LibraryRuntimeController

warnings.warn(
    "iPhoto.legacy.library.manager is deprecated. Use "
    "iPhoto.library.runtime_controller instead.",
    DeprecationWarning,
    stacklevel=2,
)

LibraryManager = LibraryRuntimeController

__all__ = ["GeotaggedAsset", "LibraryManager"]
