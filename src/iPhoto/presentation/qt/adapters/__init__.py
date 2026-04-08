"""Qt presentation adapters for library operations.

This package contains thin Qt wrapper objects that bridge application-layer
services to the Qt signal/slot system.  Adapters:

* :class:`~.library_update_adapter.LibraryUpdateAdapter`
  - relays library-update signals to the UI.
* :class:`~.scan_progress_adapter.ScanProgressAdapter`
  - relays scan progress signals to the UI.

New presentation code should depend on these adapters instead of the heavier
:class:`~iPhoto.gui.services.library_update_service.LibraryUpdateService`.
"""

from __future__ import annotations

from .library_update_adapter import LibraryUpdateAdapter
from .scan_progress_adapter import ScanProgressAdapter

__all__ = ["LibraryUpdateAdapter", "ScanProgressAdapter"]
