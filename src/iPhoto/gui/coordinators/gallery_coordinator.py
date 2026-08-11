"""Gallery-domain coordinator exposed to startup and window consumers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import QModelIndex, QObject

from iPhoto.gui.ui.models.roles import Roles


class GalleryCoordinator(QObject):
    """Own the public Gallery surface independently of Detail playback."""

    def __init__(
        self,
        *,
        context,
        facade,
        navigation,
        asset_model,
        gallery_viewmodel=None,
        library_root_getter: Callable[[], Path | None] | None = None,
        asset_query_service_getter: Callable[[], object | None] | None = None,
        asset_state_service_getter: Callable[[], object | None] | None = None,
        library_rebind_preflight: Callable[[], bool] | None = None,
        rebind_library: Callable[[], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._facade = facade
        self._navigation = navigation
        self._asset_model = asset_model
        self._gallery_viewmodel = gallery_viewmodel
        self._library_root_getter = library_root_getter
        self._asset_query_service_getter = asset_query_service_getter
        self._asset_state_service_getter = asset_state_service_getter
        self._library_rebind_preflight = library_rebind_preflight
        self._legacy_rebind_library = rebind_library

    @property
    def asset_model(self):
        return self._asset_model

    def startup_model(self):
        return self._asset_model

    def open_album_from_path(self, path: Path) -> None:
        target = Path(path).expanduser()
        if not self._ensure_session_for_open_album(target):
            return
        self._navigation.open_album(target)

    def paths_from_indexes(self, indexes: Iterable[QModelIndex]) -> list[Path]:
        paths: list[Path] = []
        for index in indexes:
            value = self._asset_model.data(index, Roles.ABS)
            if value:
                paths.append(Path(value))
        return paths

    def rebind_library(self) -> None:
        """Rebind Gallery models to the latest committed library session."""
        if self._library_root_getter is None:
            if self._legacy_rebind_library is not None:
                self._legacy_rebind_library()
            return
        root = self._library_root_getter()
        self._context.asset_runtime.bind_library_root(root)
        if self._asset_query_service_getter is not None:
            self._asset_model.rebind_asset_query_service(
                self._asset_query_service_getter(),
                root,
            )
        if self._gallery_viewmodel is not None:
            if self._asset_state_service_getter is not None:
                self._gallery_viewmodel.bind_asset_state_service(
                    self._asset_state_service_getter()
                )
            self._gallery_viewmodel.on_library_tree_updated()

    def _ensure_session_for_open_album(self, path: Path) -> bool:
        if not path.exists() or not path.is_dir():
            return True

        current_root = self._library_root()
        if current_root is not None and self._path_is_descendant(path, current_root):
            return True

        open_library = getattr(self._context, "open_library", None)
        if not callable(open_library):
            return True
        preflight = getattr(self, "_library_rebind_preflight", None)
        if preflight is not None and not preflight():
            self._facade.errorRaised.emit(
                "Finish the current edit with Done or Cancel before switching libraries."
            )
            return False
        try:
            open_library(path)
        except Exception as exc:  # noqa: BLE001 - GUI error boundary
            self._facade.errorRaised.emit(str(exc))
            return False
        self.rebind_library()
        return True

    def _library_root(self) -> Path | None:
        session = getattr(self._context, "library_session", None)
        if session is not None:
            return getattr(session, "library_root", None)
        return self._context.library.root()

    @staticmethod
    def _path_is_descendant(path: Path, root: Path) -> bool:
        try:
            Path(path).resolve().relative_to(Path(root).resolve())
        except (OSError, ValueError):
            return False
        return True


__all__ = ["GalleryCoordinator"]
